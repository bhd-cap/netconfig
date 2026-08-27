/**
 * Users tab
 *
 * Add users, change their role, activate and deactivate them, and reset
 * passwords.
 *
 * A generated or reset password is shown exactly once, in the response to the
 * call that set it. There is nowhere else to get it from afterwards, so it is
 * held on screen until dismissed rather than in a toast that disappears.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { format } from 'date-fns';
import { toast } from 'react-hot-toast';
import {
  Check,
  Copy,
  KeyRound,
  Loader2,
  Search,
  Trash2,
  UserPlus,
  UserX,
  X,
} from 'lucide-react';
import api from '../../lib/api';
import { AdminUser, PaginatedResponse, Role } from '../../types';

const PAGE_SIZE = 20;

interface OneTimePassword {
  username: string;
  password: string;
  reason: 'created' | 'reset';
}

const PasswordBanner: React.FC<{
  value: OneTimePassword;
  onDismiss: () => void;
}> = ({ value, onDismiss }) => {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value.password);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Could not copy; select the text instead');
    }
  };

  return (
    <div className="bg-amber-50 border border-amber-300 rounded-lg p-4 mb-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-amber-900">
            {value.reason === 'created'
              ? `Password for the new account '${value.username}'`
              : `New password for '${value.username}'`}
          </p>
          <p className="text-xs text-amber-800 mt-1">
            This is the only time it is shown. Hand it over now.
          </p>
          <code className="inline-block mt-2 px-3 py-1.5 bg-white border border-amber-300 rounded font-mono text-sm break-all">
            {value.password}
          </code>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={copy}
            className="inline-flex items-center px-3 py-1.5 bg-amber-600 text-white rounded text-sm hover:bg-amber-700"
          >
            {copied ? (
              <Check className="h-4 w-4 mr-1" />
            ) : (
              <Copy className="h-4 w-4 mr-1" />
            )}
            {copied ? 'Copied' : 'Copy'}
          </button>
          <button
            onClick={onDismiss}
            className="text-amber-700 hover:text-amber-900"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
      </div>
    </div>
  );
};

export const UsersTab: React.FC<{ currentUserId?: number }> = ({
  currentUserId,
}) => {
  const queryClient = useQueryClient();

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [oneTime, setOneTime] = useState<OneTimePassword | null>(null);

  const { data: roles } = useQuery<Role[]>({
    queryKey: ['roles'],
    queryFn: async () => (await api.get('/users/roles')).data,
  });

  const { data, isLoading } = useQuery<PaginatedResponse<AdminUser>>({
    queryKey: ['users', page, search],
    queryFn: async () => {
      const params: Record<string, any> = {
        skip: (page - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
      };
      if (search) params.search = search;
      return (await api.get('/users', { params })).data;
    },
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['users'] });
    queryClient.invalidateQueries({ queryKey: ['roles'] });
  };

  const createUser = useMutation({
    mutationFn: async (payload: Record<string, any>) =>
      (await api.post('/users', payload)).data,
    onSuccess: (result) => {
      invalidate();
      setShowCreate(false);
      if (result.generated_password) {
        setOneTime({
          username: result.user.username,
          password: result.generated_password,
          reason: 'created',
        });
      } else {
        toast.success(`Created ${result.user.username}`);
      }
    },
  });

  const setRole = useMutation({
    mutationFn: async ({ id, roleId }: { id: number; roleId: number | null }) =>
      (
        await api.put(`/users/${id}`,
          roleId === null ? { clear_role: true } : { role_id: roleId }
        )
      ).data,
    onSuccess: () => {
      invalidate();
      toast.success('Role updated');
    },
  });

  const setActive = useMutation({
    mutationFn: async ({ id, isActive }: { id: number; isActive: boolean }) =>
      (await api.post(`/users/${id}/activation`, { is_active: isActive })).data,
    onSuccess: (user: AdminUser) => {
      invalidate();
      toast.success(
        `${user.username} ${user.is_active ? 'activated' : 'deactivated'}`
      );
    },
  });

  const resetPassword = useMutation({
    mutationFn: async (id: number) =>
      (await api.post(`/users/${id}/reset-password`, {})).data,
    onSuccess: (result) => {
      invalidate();
      setOneTime({
        username: result.username,
        password: result.password,
        reason: 'reset',
      });
    },
  });

  const deleteUser = useMutation({
    mutationFn: async (id: number) => api.delete(`/users/${id}`),
    onSuccess: () => {
      invalidate();
      toast.success('User deleted');
    },
  });

  return (
    <div>
      {oneTime && (
        <PasswordBanner value={oneTime} onDismiss={() => setOneTime(null)} />
      )}

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="relative flex-1 min-w-[14rem]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            value={search}
            onChange={(event) => {
              setPage(1);
              setSearch(event.target.value);
            }}
            placeholder="Search by name, username or email"
            className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm"
          />
        </div>

        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
        >
          <UserPlus className="h-4 w-4 mr-2" />
          Add user
        </button>
      </div>

      {isLoading ? (
        <div className="py-12 flex justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
        </div>
      ) : (
        <>
          <div className="overflow-x-auto border border-gray-200 rounded-lg">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    User
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Role
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Last login
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500 uppercase text-xs">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {data?.items.map((user) => {
                  const isSelf = user.id === currentUserId;

                  return (
                    <tr key={user.id} className={user.is_active ? '' : 'bg-gray-50'}>
                      <td className="px-4 py-3">
                        <p className="font-medium text-gray-900">
                          {user.full_name || user.username}
                          {isSelf && (
                            <span className="ml-2 text-xs text-gray-500">(you)</span>
                          )}
                        </p>
                        <p className="text-xs text-gray-500">{user.email}</p>
                      </td>

                      <td className="px-4 py-3">
                        <select
                          value={user.role_id ?? ''}
                          disabled={setRole.isPending}
                          onChange={(event) =>
                            setRole.mutate({
                              id: user.id,
                              roleId: event.target.value
                                ? Number(event.target.value)
                                : null,
                            })
                          }
                          className="px-2 py-1 border border-gray-300 rounded text-sm"
                        >
                          <option value="">No role</option>
                          {roles?.map((role) => (
                            <option key={role.id} value={role.id}>
                              {role.name}
                            </option>
                          ))}
                        </select>
                      </td>

                      <td className="px-4 py-3">
                        <span
                          className={`px-2 py-0.5 rounded text-xs font-medium ${
                            user.is_active
                              ? 'bg-emerald-100 text-emerald-800'
                              : 'bg-gray-200 text-gray-700'
                          }`}
                        >
                          {user.is_active ? 'Active' : 'Deactivated'}
                        </span>
                        {user.must_change_password && (
                          <span className="ml-2 px-2 py-0.5 bg-amber-100 text-amber-800 rounded text-xs">
                            Must change password
                          </span>
                        )}
                      </td>

                      <td className="px-4 py-3 text-gray-500">
                        {user.last_login_at
                          ? format(new Date(user.last_login_at), 'd MMM yyyy HH:mm')
                          : 'never'}
                      </td>

                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Reset the password for ${user.username}?`
                                )
                              ) {
                                resetPassword.mutate(user.id);
                              }
                            }}
                            className="text-gray-400 hover:text-blue-600"
                            title="Reset password"
                          >
                            <KeyRound className="h-4 w-4" />
                          </button>

                          <button
                            onClick={() =>
                              setActive.mutate({
                                id: user.id,
                                isActive: !user.is_active,
                              })
                            }
                            disabled={isSelf}
                            className="text-gray-400 hover:text-amber-600 disabled:opacity-30 disabled:hover:text-gray-400"
                            title={
                              isSelf
                                ? 'You cannot deactivate your own account'
                                : user.is_active
                                ? 'Deactivate'
                                : 'Activate'
                            }
                          >
                            {user.is_active ? (
                              <UserX className="h-4 w-4" />
                            ) : (
                              <Check className="h-4 w-4" />
                            )}
                          </button>

                          <button
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Delete ${user.username}? Deactivating is usually better - it keeps their audit history readable.`
                                )
                              ) {
                                deleteUser.mutate(user.id);
                              }
                            }}
                            disabled={isSelf}
                            className="text-gray-400 hover:text-red-600 disabled:opacity-30 disabled:hover:text-gray-400"
                            title={
                              isSelf ? 'You cannot delete your own account' : 'Delete'
                            }
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {data && data.total_pages > 1 && (
            <div className="flex items-center justify-between mt-4 text-sm">
              <span className="text-gray-600">
                Page {data.page} of {data.total_pages} · {data.total} users
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  disabled={page <= 1}
                  className="px-3 py-1.5 border border-gray-300 rounded disabled:opacity-50"
                >
                  Previous
                </button>
                <button
                  onClick={() => setPage((current) => current + 1)}
                  disabled={page >= data.total_pages}
                  className="px-3 py-1.5 border border-gray-300 rounded disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {showCreate && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Add a user</h3>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                const password = String(form.get('password') ?? '').trim();

                createUser.mutate({
                  username: form.get('username'),
                  email: form.get('email'),
                  full_name: String(form.get('full_name') ?? '') || null,
                  role_id: form.get('role_id')
                    ? Number(form.get('role_id'))
                    : null,
                  // Left blank, the server generates one and returns it once.
                  password: password || undefined,
                  must_change_password: form.get('must_change') === 'on',
                });
              }}
              className="space-y-4"
            >
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Username
                </label>
                <input
                  name="username"
                  required
                  minLength={3}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Email
                </label>
                <input
                  name="email"
                  type="email"
                  required
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Full name
                </label>
                <input
                  name="full_name"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Role
                </label>
                <select
                  name="role_id"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                >
                  <option value="">No role</option>
                  {roles?.map((role) => (
                    <option key={role.id} value={role.id}>
                      {role.name} — {role.description}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Password
                </label>
                <input
                  name="password"
                  type="password"
                  minLength={8}
                  placeholder="Leave blank to generate one"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                />
              </div>

              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" name="must_change" defaultChecked />
                Require a password change at first login
              </label>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="px-4 py-2 border border-gray-300 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={createUser.isPending}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  {createUser.isPending ? 'Creating…' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
