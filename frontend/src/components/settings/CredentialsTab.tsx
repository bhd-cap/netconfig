/**
 * Credential vault
 *
 * The ordered list of logins discovery tries against a device it has just
 * found. Order matters for more than tidiness: every credential that fails
 * costs a connection timeout, so the one most likely to work belongs first.
 *
 * Secrets are write-only. A read says only whether one is stored, and an edit
 * that leaves a secret blank keeps the stored value - the same contract as
 * device passwords and backup-target keys.
 */
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'react-hot-toast';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  Radio,
  Terminal,
  Trash2,
  Zap,
} from 'lucide-react';
import api from '../../lib/api';
import { usePermissions } from '../../hooks/usePermissions';
import {
  Credential,
  CredentialKind,
  CredentialTestResult,
  Device,
  PaginatedResponse,
} from '../../types';

const KIND_LABELS: Record<CredentialKind, string> = {
  cli: 'CLI logins (SSH and telnet)',
  snmp: 'SNMP credentials',
};

const KIND_HINTS: Record<CredentialKind, string> = {
  cli: 'Tried in this order over SSH, then telnet. A device that authenticates with one of these becomes eligible for configuration backup.',
  snmp: 'Read-only. Used to inventory a device and to find devices that answer nothing else. An SNMP-only device is never backed up.',
};

function relative(value?: string | null): string {
  if (!value) return 'never';
  try {
    return formatDistanceToNow(new Date(value), { addSuffix: true });
  } catch {
    return value;
  }
}

interface FormState {
  name: string;
  description: string;
  kind: CredentialKind;
  is_enabled: boolean;
  username: string;
  password: string;
  enable_secret: string;
  clear_enable_secret: boolean;
  ssh_key_path: string;
  snmp_version: '1' | '2c' | '3';
  community: string;
  snmp_v3_user: string;
  v3_auth_key: string;
  v3_priv_key: string;
  snmp_v3_auth_protocol: string;
  snmp_v3_priv_protocol: string;
}

function blankForm(kind: CredentialKind): FormState {
  return {
    name: '',
    description: '',
    kind,
    is_enabled: true,
    username: '',
    password: '',
    enable_secret: '',
    clear_enable_secret: false,
    ssh_key_path: '',
    snmp_version: '2c',
    community: '',
    snmp_v3_user: '',
    v3_auth_key: '',
    v3_priv_key: '',
    snmp_v3_auth_protocol: 'SHA',
    snmp_v3_priv_protocol: 'AES',
  };
}

function formFor(credential: Credential): FormState {
  return {
    ...blankForm(credential.kind),
    name: credential.name,
    description: credential.description ?? '',
    is_enabled: credential.is_enabled,
    username: credential.username ?? '',
    ssh_key_path: credential.ssh_key_path ?? '',
    snmp_version: (credential.snmp_version ?? '2c') as '1' | '2c' | '3',
    snmp_v3_user: credential.snmp_v3_user ?? '',
    snmp_v3_auth_protocol: credential.snmp_v3_auth_protocol ?? 'SHA',
    snmp_v3_priv_protocol: credential.snmp_v3_priv_protocol ?? 'AES',
  };
}

export const CredentialsTab: React.FC = () => {
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const canWrite = can('credentials:write');
  const canDelete = can('credentials:delete');

  const [editing, setEditing] = useState<Credential | null>(null);
  const [creatingKind, setCreatingKind] = useState<CredentialKind | null>(null);
  const [testing, setTesting] = useState<Credential | null>(null);

  const { data, isLoading } = useQuery<Credential[]>({
    queryKey: ['credentials'],
    queryFn: async () => (await api.get('/credentials')).data,
  });

  const byKind = useMemo(() => {
    const groups: Record<CredentialKind, Credential[]> = { cli: [], snmp: [] };
    (data ?? []).forEach((credential) => groups[credential.kind].push(credential));
    return groups;
  }, [data]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['credentials'] });
    queryClient.invalidateQueries({ queryKey: ['credential-summary'] });
  };

  const saveMutation = useMutation({
    mutationFn: async ({
      credential,
      form,
    }: {
      credential: Credential | null;
      form: FormState;
    }) => {
      // A blank secret means "keep the stored one", so it is dropped from the
      // payload rather than sent empty.
      const payload: Record<string, any> = {
        name: form.name,
        description: form.description || null,
        is_enabled: form.is_enabled,
      };

      if (form.kind === 'cli') {
        payload.username = form.username;
        if (form.ssh_key_path) payload.ssh_key_path = form.ssh_key_path;
        if (form.password) payload.password = form.password;
        if (form.enable_secret) payload.enable_secret = form.enable_secret;
        if (credential && form.clear_enable_secret) {
          payload.clear_enable_secret = true;
        }
      } else {
        payload.snmp_version = form.snmp_version;
        if (form.snmp_version === '3') {
          payload.snmp_v3_user = form.snmp_v3_user;
          payload.snmp_v3_auth_protocol = form.snmp_v3_auth_protocol;
          payload.snmp_v3_priv_protocol = form.snmp_v3_priv_protocol;
          if (form.v3_auth_key) payload.v3_auth_key = form.v3_auth_key;
          if (form.v3_priv_key) payload.v3_priv_key = form.v3_priv_key;
        } else if (form.community) {
          payload.community = form.community;
        }
      }

      if (credential) {
        return (await api.put(`/credentials/${credential.id}`, payload)).data;
      }

      return (await api.post('/credentials', { ...payload, kind: form.kind })).data;
    },
    onSuccess: (_result, variables) => {
      invalidate();
      setEditing(null);
      setCreatingKind(null);
      toast.success(variables.credential ? 'Credential updated' : 'Credential added');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Could not save the credential');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (credential: Credential) =>
      api.delete(`/credentials/${credential.id}`),
    onSuccess: () => {
      invalidate();
      toast.success('Credential deleted');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Could not delete it');
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async (credential: Credential) =>
      api.put(`/credentials/${credential.id}`, {
        is_enabled: !credential.is_enabled,
      }),
    onSuccess: invalidate,
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Could not change it');
    },
  });

  const reorderMutation = useMutation({
    mutationFn: async (credentialIds: number[]) =>
      api.post('/credentials/reorder', { credential_ids: credentialIds }),
    onSuccess: invalidate,
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Could not reorder them');
    },
  });

  /** Move one credential within its own kind; the other kind is untouched. */
  const move = (kind: CredentialKind, index: number, delta: number) => {
    const group = byKind[kind];
    const target = index + delta;
    if (target < 0 || target >= group.length) return;

    const order = group.map((credential) => credential.id);
    [order[index], order[target]] = [order[target], order[index]];
    reorderMutation.mutate(order);
  };

  if (isLoading) {
    return (
      <div className="py-12 flex justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-gray-900 flex items-center">
          <KeyRound className="h-5 w-5 mr-2 text-gray-400" />
          Credential vault
        </h2>
        <p className="text-sm text-gray-600 mt-1">
          What discovery tries when it finds a device. Each one is attempted in
          turn until a login succeeds; a device that never authenticates stays in
          the inventory but is left off the backup list.
        </p>
      </div>

      {(['cli', 'snmp'] as CredentialKind[]).map((kind) => {
        const group = byKind[kind];

        return (
          <section key={kind}>
            <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
              <div>
                <h3 className="font-medium text-gray-900 flex items-center">
                  {kind === 'cli' ? (
                    <Terminal className="h-4 w-4 mr-2 text-gray-400" />
                  ) : (
                    <Radio className="h-4 w-4 mr-2 text-gray-400" />
                  )}
                  {KIND_LABELS[kind]}
                </h3>
                <p className="text-xs text-gray-500 mt-1 max-w-2xl">
                  {KIND_HINTS[kind]}
                </p>
              </div>

              {canWrite && (
                <button
                  onClick={() => setCreatingKind(kind)}
                  data-testid={`add-credential-${kind}`}
                  className="inline-flex items-center px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
                >
                  <Plus className="h-4 w-4 mr-2" />
                  Add
                </button>
              )}
            </div>

            {group.length === 0 ? (
              <div className="border border-dashed border-gray-300 rounded-lg p-6 text-center text-sm text-gray-500">
                {kind === 'cli'
                  ? 'No CLI logins yet. Without one, a crawl maps the topology but authenticates nothing.'
                  : 'No SNMP credentials yet. Add one to inventory devices that only answer SNMP.'}
              </div>
            ) : (
              <div className="overflow-x-auto border border-gray-200 rounded-lg">
                <table className="min-w-full divide-y divide-gray-200 text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase w-16">
                        Order
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Name
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        {kind === 'cli' ? 'User' : 'Version'}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Stored
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Worked
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        State
                      </th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {group.map((credential, index) => (
                      <tr
                        key={credential.id}
                        className={credential.is_enabled ? '' : 'bg-gray-50'}
                      >
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-1">
                            <span className="text-gray-500 tabular-nums">
                              {index + 1}
                            </span>
                            {canWrite && group.length > 1 && (
                              <div className="flex flex-col">
                                <button
                                  onClick={() => move(kind, index, -1)}
                                  disabled={index === 0 || reorderMutation.isPending}
                                  className="text-gray-400 hover:text-blue-600 disabled:opacity-30"
                                  title="Try this one earlier"
                                >
                                  <ArrowUp className="h-3 w-3" />
                                </button>
                                <button
                                  onClick={() => move(kind, index, 1)}
                                  disabled={
                                    index === group.length - 1 ||
                                    reorderMutation.isPending
                                  }
                                  className="text-gray-400 hover:text-blue-600 disabled:opacity-30"
                                  title="Try this one later"
                                >
                                  <ArrowDown className="h-3 w-3" />
                                </button>
                              </div>
                            )}
                          </div>
                        </td>

                        <td className="px-3 py-2">
                          <p className="font-medium text-gray-900">
                            {credential.name}
                          </p>
                          {credential.description && (
                            <p className="text-xs text-gray-500">
                              {credential.description}
                            </p>
                          )}
                        </td>

                        <td className="px-3 py-2 text-gray-700">
                          {kind === 'cli'
                            ? credential.username || '—'
                            : `v${credential.snmp_version ?? '?'}`}
                        </td>

                        <td className="px-3 py-2 text-xs text-gray-600">
                          {[
                            credential.has_password && 'password',
                            credential.has_enable_secret && 'enable secret',
                            credential.ssh_key_path && 'SSH key',
                            credential.has_community && 'community',
                            credential.has_v3_auth_key && 'auth key',
                            credential.has_v3_priv_key && 'privacy key',
                          ]
                            .filter(Boolean)
                            .join(', ') || '—'}
                        </td>

                        <td className="px-3 py-2 text-xs text-gray-600">
                          <span className="text-emerald-700">
                            {credential.success_count}
                          </span>
                          {' / '}
                          <span className="text-amber-700">
                            {credential.failure_count}
                          </span>
                          <p className="text-gray-400">
                            last {relative(credential.last_success_at)}
                          </p>
                        </td>

                        <td className="px-3 py-2">
                          {canWrite ? (
                            <button
                              onClick={() => toggleMutation.mutate(credential)}
                              className={`px-2 py-0.5 rounded text-xs font-medium ${
                                credential.is_enabled
                                  ? 'bg-emerald-100 text-emerald-800'
                                  : 'bg-gray-200 text-gray-700'
                              }`}
                              title={
                                credential.is_enabled
                                  ? 'Stop trying this one'
                                  : 'Start trying this one again'
                              }
                            >
                              {credential.is_enabled ? 'enabled' : 'disabled'}
                            </button>
                          ) : (
                            <span className="text-xs text-gray-600">
                              {credential.is_enabled ? 'enabled' : 'disabled'}
                            </span>
                          )}
                        </td>

                        <td className="px-3 py-2">
                          <div className="flex justify-end gap-2">
                            {canWrite && (
                              <>
                                <button
                                  onClick={() => setTesting(credential)}
                                  className="text-gray-400 hover:text-purple-600"
                                  title="Try it against a device"
                                >
                                  <Zap className="h-4 w-4" />
                                </button>
                                <button
                                  onClick={() => setEditing(credential)}
                                  className="text-gray-400 hover:text-blue-600"
                                  title="Edit"
                                >
                                  <Pencil className="h-4 w-4" />
                                </button>
                              </>
                            )}
                            {canDelete && (
                              <button
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `Delete '${credential.name}'? Devices that already ` +
                                        'authenticated with it keep working.'
                                    )
                                  ) {
                                    deleteMutation.mutate(credential);
                                  }
                                }}
                                className="text-gray-400 hover:text-red-600"
                                title="Delete"
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}

      {(editing || creatingKind) && (
        <CredentialModal
          credential={editing}
          kind={editing?.kind ?? creatingKind!}
          isSaving={saveMutation.isPending}
          onClose={() => {
            setEditing(null);
            setCreatingKind(null);
          }}
          onSubmit={(form) => saveMutation.mutate({ credential: editing, form })}
        />
      )}

      {testing && (
        <TestModal credential={testing} onClose={() => setTesting(null)} />
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Add / edit
// ---------------------------------------------------------------------------

interface ModalProps {
  credential: Credential | null;
  kind: CredentialKind;
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (form: FormState) => void;
}

const CredentialModal: React.FC<ModalProps> = ({
  credential,
  kind,
  isSaving,
  onClose,
  onSubmit,
}) => {
  const [form, setForm] = useState<FormState>(
    credential ? formFor(credential) : blankForm(kind)
  );

  const set = <K extends keyof FormState>(field: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [field]: value }));

  const keepHint = credential ? 'Leave blank to keep the stored one' : '';

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg my-8">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(form);
          }}
          className="p-6 space-y-4"
        >
          <div>
            <h3 className="text-lg font-semibold text-gray-900">
              {credential ? `Edit '${credential.name}'` : 'Add a credential'}
            </h3>
            <p className="text-xs text-gray-500 mt-1">{KIND_HINTS[form.kind]}</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Name *
            </label>
            <input
              required
              value={form.name}
              onChange={(event) => set('name', event.target.value)}
              placeholder={form.kind === 'cli' ? 'Network admin' : 'Read-only community'}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Description
            </label>
            <input
              value={form.description}
              onChange={(event) => set('description', event.target.value)}
              placeholder="Where this login comes from, or which sites use it"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          {form.kind === 'cli' ? (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Username *
                  </label>
                  <input
                    required
                    data-testid="credential-username"
                    value={form.username}
                    onChange={(event) => set('username', event.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Password {credential ? '' : '*'}
                  </label>
                  <input
                    type="password"
                    required={!credential && !form.ssh_key_path}
                    data-testid="credential-password"
                    value={form.password}
                    onChange={(event) => set('password', event.target.value)}
                    placeholder={keepHint}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Enable secret
                </label>
                <input
                  type="password"
                  value={form.enable_secret}
                  disabled={form.clear_enable_secret}
                  onChange={(event) => set('enable_secret', event.target.value)}
                  placeholder={keepHint || 'For devices that need enable mode'}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg disabled:bg-gray-100"
                />
                {credential?.has_enable_secret && (
                  <label className="flex items-center gap-2 mt-2 text-xs text-gray-600">
                    <input
                      type="checkbox"
                      checked={form.clear_enable_secret}
                      onChange={(event) =>
                        set('clear_enable_secret', event.target.checked)
                      }
                    />
                    Remove the stored enable secret
                  </label>
                )}
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  SSH key path
                </label>
                <input
                  value={form.ssh_key_path}
                  onChange={(event) => set('ssh_key_path', event.target.value)}
                  placeholder="/etc/netconfig-backup/keys/id_ed25519"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                />
                <p className="text-xs text-gray-500 mt-1">
                  A path on the server. Either a password or a key is required.
                </p>
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Version
                </label>
                <select
                  value={form.snmp_version}
                  onChange={(event) =>
                    set('snmp_version', event.target.value as '1' | '2c' | '3')
                  }
                  disabled={Boolean(credential)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg disabled:bg-gray-100"
                >
                  <option value="1">v1</option>
                  <option value="2c">v2c</option>
                  <option value="3">v3</option>
                </select>
              </div>

              {form.snmp_version === '3' ? (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      v3 username *
                    </label>
                    <input
                      required
                      value={form.snmp_v3_user}
                      onChange={(event) => set('snmp_v3_user', event.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Auth protocol
                      </label>
                      <select
                        value={form.snmp_v3_auth_protocol}
                        onChange={(event) =>
                          set('snmp_v3_auth_protocol', event.target.value)
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                      >
                        <option value="SHA">SHA</option>
                        <option value="MD5">MD5</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Privacy protocol
                      </label>
                      <select
                        value={form.snmp_v3_priv_protocol}
                        onChange={(event) =>
                          set('snmp_v3_priv_protocol', event.target.value)
                        }
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                      >
                        <option value="AES">AES</option>
                        <option value="DES">DES</option>
                      </select>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Auth key {credential ? '' : '*'}
                      </label>
                      <input
                        type="password"
                        required={!credential}
                        value={form.v3_auth_key}
                        onChange={(event) => set('v3_auth_key', event.target.value)}
                        placeholder={keepHint}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Privacy key
                      </label>
                      <input
                        type="password"
                        value={form.v3_priv_key}
                        onChange={(event) => set('v3_priv_key', event.target.value)}
                        placeholder={keepHint}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                      />
                    </div>
                  </div>
                </>
              ) : (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Community {credential ? '' : '*'}
                  </label>
                  <input
                    type="password"
                    required={!credential}
                    value={form.community}
                    onChange={(event) => set('community', event.target.value)}
                    placeholder={keepHint || 'public'}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  />
                </div>
              )}
            </>
          )}

          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={form.is_enabled}
              onChange={(event) => set('is_enabled', event.target.checked)}
            />
            Try this credential during discovery
          </label>

          <div className="flex justify-end gap-3 pt-4 border-t">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSaving}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {isSaving ? 'Saving…' : credential ? 'Save' : 'Add credential'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Try it against a device
// ---------------------------------------------------------------------------

const TestModal: React.FC<{ credential: Credential; onClose: () => void }> = ({
  credential,
  onClose,
}) => {
  const [deviceId, setDeviceId] = useState<number | ''>('');
  const [result, setResult] = useState<CredentialTestResult | null>(null);

  const { data: devices } = useQuery<PaginatedResponse<Device>>({
    queryKey: ['devices', 'for-credential-test'],
    queryFn: async () => (await api.get('/devices', { params: { limit: 100 } })).data,
  });

  const test = useMutation({
    mutationFn: async () =>
      (
        await api.post(`/credentials/${credential.id}/test`, null, {
          params: { device_id: deviceId },
        })
      ).data as CredentialTestResult,
    onSuccess: setResult,
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Could not run the test');
    },
  });

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">
            Try '{credential.name}'
          </h3>
          <p className="text-xs text-gray-500 mt-1">
            One login attempt against one device. Nothing is changed on the
            device, and the outcome is recorded against the credential.
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Device
          </label>
          <select
            value={deviceId}
            onChange={(event) =>
              setDeviceId(event.target.value ? Number(event.target.value) : '')
            }
            className="w-full px-3 py-2 border border-gray-300 rounded-lg"
          >
            <option value="">Pick a device</option>
            {devices?.items.map((device) => (
              <option key={device.id} value={device.id}>
                {device.hostname} ({device.ip_address})
              </option>
            ))}
          </select>
        </div>

        {result && (
          <div
            className={`rounded border p-3 text-sm ${
              result.success
                ? 'border-emerald-200 bg-emerald-50'
                : 'border-amber-200 bg-amber-50'
            }`}
          >
            <p className="flex items-center font-medium">
              {result.success ? (
                <CheckCircle2 className="h-4 w-4 mr-2 text-emerald-600" />
              ) : (
                <AlertTriangle className="h-4 w-4 mr-2 text-amber-600" />
              )}
              {result.success
                ? `Authenticated over ${result.transport}`
                : `${result.result.replace('_', ' ')} on ${result.device}`}
            </p>
            <p className="text-xs text-gray-700 mt-1 break-words">
              {result.message}
            </p>
            {Object.keys(result.facts ?? {}).length > 0 && (
              <pre className="mt-2 text-xs text-gray-600 whitespace-pre-wrap font-mono">
                {JSON.stringify(result.facts, null, 2)}
              </pre>
            )}
          </div>
        )}

        <div className="flex justify-end gap-3 pt-2 border-t">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
          >
            Close
          </button>
          <button
            onClick={() => test.mutate()}
            disabled={!deviceId || test.isPending}
            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {test.isPending ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Zap className="h-4 w-4 mr-2" />
            )}
            {test.isPending ? 'Trying…' : 'Try it'}
          </button>
        </div>
      </div>
    </div>
  );
};
