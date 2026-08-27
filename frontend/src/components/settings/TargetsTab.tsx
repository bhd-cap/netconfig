/**
 * Remote backup targets tab
 *
 * SFTP and FTP servers that stored configurations are copied to. The copy
 * runs as its own task after a backup, so an archive server being down never
 * fails the backup itself.
 *
 * Passwords and private keys are write-only: a read says only whether one is
 * stored, and leaving the field blank on an edit keeps what is there.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'react-hot-toast';
import {
  CheckCircle2,
  HardDriveUpload,
  Loader2,
  Plug,
  Plus,
  Server,
  Trash2,
  XCircle,
} from 'lucide-react';
import api from '../../lib/api';
import { BackupTarget } from '../../types';

const DEFAULT_PORTS: Record<string, number> = { sftp: 22, ftp: 21, ftps: 21 };

export const TargetsTab: React.FC = () => {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<BackupTarget | 'new' | null>(null);

  const { data: targets, isLoading } = useQuery<BackupTarget[]>({
    queryKey: ['backup-targets'],
    queryFn: async () => (await api.get('/settings/targets')).data,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['backup-targets'] });

  const saveTarget = useMutation({
    mutationFn: async ({
      id,
      payload,
    }: {
      id?: number;
      payload: Record<string, any>;
    }) =>
      id
        ? (await api.put(`/settings/targets/${id}`, payload)).data
        : (await api.post('/settings/targets', payload)).data,
    onSuccess: () => {
      invalidate();
      setEditing(null);
      toast.success('Target saved');
    },
  });

  const deleteTarget = useMutation({
    mutationFn: async (id: number) => api.delete(`/settings/targets/${id}`),
    onSuccess: () => {
      invalidate();
      toast.success('Target deleted');
    },
  });

  const testTarget = useMutation({
    mutationFn: async (id: number) =>
      (await api.post(`/settings/targets/${id}/test`)).data,
    onSuccess: (result) => {
      invalidate();
      if (result.success) toast.success(result.message ?? 'Connected');
      else toast.error(result.message ?? 'Could not connect');
    },
  });

  const uploadNow = useMutation({
    mutationFn: async (id: number) =>
      (await api.post(`/settings/targets/${id}/upload`, { run_async: true })).data,
    onSuccess: (result) => toast.success(result.message ?? 'Upload queued'),
  });

  if (isLoading) {
    return (
      <div className="py-12 flex justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
      </div>
    );
  }

  const target = editing === 'new' ? null : editing;

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">
            Remote backup targets
          </h3>
          <p className="text-sm text-gray-500">
            Copy every stored configuration to an SFTP or FTP archive.
          </p>
        </div>

        <button
          onClick={() => setEditing('new')}
          className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
        >
          <Plus className="h-4 w-4 mr-2" />
          Add target
        </button>
      </div>

      {!targets?.length ? (
        <div className="border border-dashed border-gray-300 rounded-lg p-12 text-center text-gray-500">
          <Server className="h-10 w-10 mx-auto mb-3 text-gray-300" />
          <p className="font-medium text-gray-900">No targets configured</p>
          <p className="text-sm mt-1">
            Backups are kept locally until you add somewhere to copy them.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {targets.map((entry) => (
            <div
              key={entry.id}
              className="border border-gray-200 rounded-lg p-4 flex flex-wrap items-start gap-4"
            >
              <div className="flex-1 min-w-[16rem]">
                <div className="flex items-center gap-2">
                  <h4 className="font-medium text-gray-900">{entry.name}</h4>
                  {!entry.is_enabled && (
                    <span className="px-2 py-0.5 bg-gray-200 text-gray-700 rounded text-xs">
                      disabled
                    </span>
                  )}
                  {entry.upload_on_backup && entry.is_enabled && (
                    <span className="px-2 py-0.5 bg-blue-100 text-blue-800 rounded text-xs">
                      copies on every backup
                    </span>
                  )}
                </div>

                <p className="text-sm text-gray-600 font-mono mt-1">
                  {entry.protocol}://{entry.username}@{entry.host}:{entry.port}
                  {entry.remote_path}
                </p>

                <p className="text-xs text-gray-500 mt-1">
                  {entry.has_private_key ? 'Key authentication' : 'Password authentication'}
                  {entry.use_device_subdirectories &&
                    ' · mirrors the {org}/{hostname}/ layout'}
                </p>

                <div className="flex flex-wrap items-center gap-3 mt-2 text-xs">
                  {entry.last_status === 'success' && (
                    <span className="inline-flex items-center text-emerald-700">
                      <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                      Last run succeeded
                    </span>
                  )}
                  {entry.last_status === 'failed' && (
                    <span className="inline-flex items-center text-red-700">
                      <XCircle className="h-3.5 w-3.5 mr-1" />
                      {entry.last_error || 'Last run failed'}
                    </span>
                  )}
                  {entry.last_run_at && (
                    <span className="text-gray-500">
                      {formatDistanceToNow(new Date(entry.last_run_at), {
                        addSuffix: true,
                      })}
                    </span>
                  )}
                  <span className="text-gray-500">
                    {entry.uploads_succeeded} uploaded, {entry.uploads_failed} failed
                  </span>
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => testTarget.mutate(entry.id)}
                  disabled={testTarget.isPending}
                  className="inline-flex items-center px-3 py-1.5 border border-gray-300 rounded text-sm hover:bg-gray-50 disabled:opacity-50"
                >
                  <Plug className="h-4 w-4 mr-1.5" />
                  Test
                </button>

                <button
                  onClick={() => uploadNow.mutate(entry.id)}
                  disabled={!entry.is_enabled || uploadNow.isPending}
                  className="inline-flex items-center px-3 py-1.5 border border-gray-300 rounded text-sm hover:bg-gray-50 disabled:opacity-50"
                  title="Send the latest backup of every device"
                >
                  <HardDriveUpload className="h-4 w-4 mr-1.5" />
                  Upload now
                </button>

                <button
                  onClick={() => setEditing(entry)}
                  className="px-3 py-1.5 border border-gray-300 rounded text-sm hover:bg-gray-50"
                >
                  Edit
                </button>

                <button
                  onClick={() => {
                    if (window.confirm(`Delete the target '${entry.name}'?`)) {
                      deleteTarget.mutate(entry.id);
                    }
                  }}
                  className="px-3 py-1.5 border border-red-300 text-red-700 rounded text-sm hover:bg-red-50"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4 overflow-y-auto">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-6 my-8">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              {target ? `Edit '${target.name}'` : 'Add a backup target'}
            </h3>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                const protocol = String(form.get('protocol'));
                const password = String(form.get('password') ?? '').trim();
                const privateKey = String(form.get('private_key') ?? '').trim();

                const payload: Record<string, any> = {
                  name: form.get('name'),
                  protocol,
                  host: form.get('host'),
                  port: Number(form.get('port')) || DEFAULT_PORTS[protocol],
                  username: form.get('username'),
                  remote_path: String(form.get('remote_path') || '/'),
                  use_device_subdirectories: form.get('subdirs') === 'on',
                  is_enabled: form.get('enabled') === 'on',
                  upload_on_backup: form.get('on_backup') === 'on',
                };

                if (password) payload.password = password;
                if (privateKey) payload.private_key = privateKey;

                saveTarget.mutate({ id: target?.id, payload });
              }}
              className="space-y-4"
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="sm:col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Name
                  </label>
                  <input
                    name="name"
                    required
                    defaultValue={target?.name}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Protocol
                  </label>
                  <select
                    name="protocol"
                    defaultValue={target?.protocol ?? 'sftp'}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  >
                    <option value="sftp">SFTP (over SSH)</option>
                    <option value="ftp">FTP</option>
                    <option value="ftps">FTPS</option>
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Port
                  </label>
                  <input
                    name="port"
                    type="number"
                    defaultValue={target?.port ?? 22}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Host
                  </label>
                  <input
                    name="host"
                    required
                    defaultValue={target?.host}
                    placeholder="archive.example.com"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Username
                  </label>
                  <input
                    name="username"
                    required
                    defaultValue={target?.username}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Password
                  </label>
                  <input
                    name="password"
                    type="password"
                    placeholder={
                      target?.has_password ? 'stored — blank keeps it' : ''
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Remote path
                  </label>
                  <input
                    name="remote_path"
                    defaultValue={target?.remote_path ?? '/'}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Private key (SFTP only)
                  </label>
                  <textarea
                    name="private_key"
                    rows={3}
                    placeholder={
                      target?.has_private_key
                        ? 'stored — blank keeps it'
                        : '-----BEGIN OPENSSH PRIVATE KEY-----'
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg font-mono text-xs"
                  />
                </div>
              </div>

              <div className="space-y-2 text-sm">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    name="enabled"
                    defaultChecked={target?.is_enabled ?? true}
                  />
                  Enabled
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    name="on_backup"
                    defaultChecked={target?.upload_on_backup ?? true}
                  />
                  Copy each configuration as soon as it is stored
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    name="subdirs"
                    defaultChecked={target?.use_device_subdirectories ?? true}
                  />
                  Mirror the {'{organization}/{hostname}/'} layout on the remote
                </label>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setEditing(null)}
                  className="px-4 py-2 border border-gray-300 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saveTarget.isPending}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  {saveTarget.isPending ? 'Saving…' : 'Save'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
