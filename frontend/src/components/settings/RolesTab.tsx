/**
 * Roles tab
 *
 * A role is a named set of permissions. The catalogue comes from the API, so
 * the editor can never offer a permission the server would reject.
 *
 * The built-in roles can have their permissions tuned but cannot be renamed
 * or deleted, which is what stops an organization from removing its own
 * ability to administer itself.
 */
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import { Loader2, Lock, Plus, Save, Shield, Trash2 } from 'lucide-react';
import api from '../../lib/api';
import { PermissionEntry, Role } from '../../types';

export const RolesTab: React.FC = () => {
  const queryClient = useQueryClient();

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Set<string> | null>(null);

  const { data: roles, isLoading } = useQuery<Role[]>({
    queryKey: ['roles'],
    queryFn: async () => (await api.get('/users/roles')).data,
  });

  const { data: catalogue } = useQuery<PermissionEntry[]>({
    queryKey: ['permission-catalogue'],
    queryFn: async () => (await api.get('/users/permissions')).data,
    staleTime: Infinity,
  });

  const byResource = useMemo(() => {
    const grouped = new Map<string, PermissionEntry[]>();
    (catalogue ?? []).forEach((entry) => {
      const list = grouped.get(entry.resource) ?? [];
      list.push(entry);
      grouped.set(entry.resource, list);
    });
    return grouped;
  }, [catalogue]);

  const selected = roles?.find((role) => role.id === selectedId) ?? null;

  /**
   * Which concrete permissions a role's stored list grants
   *
   * Wildcards are expanded here so the checkboxes reflect what the role can
   * actually do, not the shorthand it happens to be stored as.
   */
  const grantedFor = (role: Role): Set<string> => {
    if (role.permissions.includes('*')) {
      return new Set((catalogue ?? []).map((entry) => entry.permission));
    }

    const granted = new Set<string>();
    role.permissions.forEach((permission) => {
      if (permission.endsWith(':*')) {
        const resource = permission.slice(0, -2);
        (byResource.get(resource) ?? []).forEach((entry) =>
          granted.add(entry.permission)
        );
      } else {
        granted.add(permission);
      }
    });
    return granted;
  };

  const select = (role: Role) => {
    setSelectedId(role.id);
    setDraft(grantedFor(role));
  };

  const toggle = (permission: string) => {
    setDraft((current) => {
      const next = new Set(current ?? []);
      if (next.has(permission)) next.delete(permission);
      else next.add(permission);
      return next;
    });
  };

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['roles'] });
    queryClient.invalidateQueries({ queryKey: ['me'] });
    queryClient.invalidateQueries({ queryKey: ['users'] });
  };

  const saveRole = useMutation({
    mutationFn: async () => {
      if (!selected || !draft) throw new Error('Nothing selected');
      return (
        await api.put(`/users/roles/${selected.id}`, {
          permissions: Array.from(draft).sort(),
        })
      ).data;
    },
    onSuccess: () => {
      invalidate();
      toast.success('Role saved');
    },
  });

  const createRole = useMutation({
    mutationFn: async (payload: { name: string; description: string }) =>
      (
        await api.post('/users/roles', {
          ...payload,
          permissions: ['devices:read', 'backups:read'],
        })
      ).data as Role,
    onSuccess: (role) => {
      invalidate();
      select(role);
      toast.success(`Created '${role.name}'`);
    },
  });

  const deleteRole = useMutation({
    mutationFn: async (id: number) => api.delete(`/users/roles/${id}`),
    onSuccess: () => {
      invalidate();
      setSelectedId(null);
      setDraft(null);
      toast.success('Role deleted');
    },
  });

  const handleCreate = () => {
    const name = window.prompt('Name for the new role');
    if (!name?.trim()) return;
    createRole.mutate({ name: name.trim(), description: '' });
  };

  if (isLoading) {
    return (
      <div className="py-12 flex justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
      </div>
    );
  }

  const isWildcardRole = selected?.permissions.includes('*') ?? false;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Role list */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-500 uppercase">Roles</h3>
          <button
            onClick={handleCreate}
            className="inline-flex items-center px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
          >
            <Plus className="h-3 w-3 mr-1" />
            New
          </button>
        </div>

        <ul className="space-y-2">
          {roles?.map((role) => (
            <li key={role.id}>
              <button
                onClick={() => select(role)}
                className={`w-full text-left px-3 py-2 rounded-lg border transition ${
                  selectedId === role.id
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-gray-900 flex items-center">
                    {role.is_system ? (
                      <Lock className="h-3.5 w-3.5 mr-1.5 text-gray-400" />
                    ) : (
                      <Shield className="h-3.5 w-3.5 mr-1.5 text-gray-400" />
                    )}
                    {role.name}
                  </span>
                  <span className="text-xs text-gray-500">
                    {role.user_count} user{role.user_count === 1 ? '' : 's'}
                  </span>
                </div>
                {role.description && (
                  <p className="text-xs text-gray-500 mt-1">{role.description}</p>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* Permission editor */}
      <div className="lg:col-span-2">
        {!selected ? (
          <div className="border border-dashed border-gray-300 rounded-lg p-12 text-center text-gray-500">
            <Shield className="h-10 w-10 mx-auto mb-3 text-gray-300" />
            <p>Pick a role to see and change what it grants.</p>
          </div>
        ) : (
          <div>
            <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-900">
                  {selected.name}
                </h3>
                <p className="text-sm text-gray-500">
                  {selected.is_system
                    ? 'Built in: its permissions can be tuned, but it cannot be renamed or deleted.'
                    : `${selected.user_count} user(s) hold this role`}
                </p>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={() => saveRole.mutate()}
                  disabled={saveRole.isPending}
                  className="inline-flex items-center px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
                >
                  <Save className="h-4 w-4 mr-2" />
                  Save
                </button>

                {!selected.is_system && (
                  <button
                    onClick={() => {
                      if (window.confirm(`Delete the role '${selected.name}'?`)) {
                        deleteRole.mutate(selected.id);
                      }
                    }}
                    className="inline-flex items-center px-3 py-2 border border-red-300 text-red-700 rounded-lg text-sm hover:bg-red-50"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>

            {isWildcardRole && (
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 text-sm text-blue-800">
                This role holds the <code>*</code> wildcard, so it gains any
                permission added in a later release. Saving replaces that with the
                explicit list below.
              </div>
            )}

            <div className="space-y-4">
              {Array.from(byResource.entries()).map(([resource, entries]) => (
                <div key={resource} className="border border-gray-200 rounded-lg p-4">
                  <h4 className="text-sm font-semibold text-gray-900 capitalize mb-3">
                    {resource}
                  </h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {entries.map((entry) => (
                      <label
                        key={entry.permission}
                        className="flex items-start gap-2 text-sm cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          checked={draft?.has(entry.permission) ?? false}
                          onChange={() => toggle(entry.permission)}
                        />
                        <span>
                          <span className="text-gray-900">{entry.description}</span>
                          <code className="block text-xs text-gray-400">
                            {entry.permission}
                          </code>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
