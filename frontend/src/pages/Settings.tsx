/**
 * Settings Page
 *
 * Tabs for the caller's own profile, user and role administration, the
 * organization's application settings, and the remote backup targets.
 *
 * Which tabs appear is decided by the permissions the caller actually holds,
 * so the page never offers an action the API would refuse.
 */
import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import {
  HardDriveUpload,
  KeyRound,
  Loader2,
  Lock,
  Settings as SettingsIcon,
  Shield,
  User,
  Users,
} from 'lucide-react';
import api from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import { usePermissions } from '../hooks/usePermissions';
import { ApplicationTab } from '../components/settings/ApplicationTab';
import { CredentialsTab } from '../components/settings/CredentialsTab';
import { RolesTab } from '../components/settings/RolesTab';
import { TargetsTab } from '../components/settings/TargetsTab';
import { UsersTab } from '../components/settings/UsersTab';

type TabId =
  | 'profile'
  | 'users'
  | 'roles'
  | 'credentials'
  | 'application'
  | 'targets';

interface Tab {
  id: TabId;
  label: string;
  icon: React.ElementType;
  permission?: string;
}

const TABS: Tab[] = [
  { id: 'profile', label: 'Profile', icon: User },
  { id: 'users', label: 'Users', icon: Users, permission: 'users:read' },
  { id: 'roles', label: 'Roles', icon: Shield, permission: 'users:read' },
  {
    id: 'credentials',
    label: 'Credentials',
    icon: KeyRound,
    permission: 'credentials:read',
  },
  {
    id: 'application',
    label: 'Application',
    icon: SettingsIcon,
    permission: 'settings:read',
  },
  {
    id: 'targets',
    label: 'Backup targets',
    icon: HardDriveUpload,
    permission: 'targets:read',
  },
];

const ProfileTab: React.FC = () => {
  const { user } = useAuth();
  const { me, isLoading } = usePermissions();
  const queryClient = useQueryClient();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const changePassword = useMutation({
    mutationFn: async () =>
      api.post('/users/me/password', {
        current_password: currentPassword,
        new_password: newPassword,
      }),
    onSuccess: () => {
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      queryClient.invalidateQueries({ queryKey: ['me'] });
      toast.success('Password changed');
    },
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();

    if (newPassword !== confirmPassword) {
      toast.error('The new passwords do not match');
      return;
    }
    if (newPassword.length < 8) {
      toast.error('The new password must be at least 8 characters');
      return;
    }

    changePassword.mutate();
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
      <section>
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Your account</h3>

        <dl className="space-y-3 max-w-md text-sm">
          <div className="flex justify-between border-b border-gray-100 pb-2">
            <dt className="text-gray-500">Username</dt>
            <dd className="text-gray-900 font-medium">{me?.username ?? user?.username}</dd>
          </div>
          <div className="flex justify-between border-b border-gray-100 pb-2">
            <dt className="text-gray-500">Email</dt>
            <dd className="text-gray-900">{me?.email ?? user?.email}</dd>
          </div>
          <div className="flex justify-between border-b border-gray-100 pb-2">
            <dt className="text-gray-500">Role</dt>
            <dd className="text-gray-900">
              {me?.role?.name ?? (user?.is_admin ? 'Administrator' : 'No role')}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">Permissions</dt>
            <dd className="text-gray-900">{me?.permissions.length ?? 0} granted</dd>
          </div>
        </dl>

        {me && me.permissions.length > 0 && (
          <details className="mt-4 max-w-md">
            <summary className="text-sm text-blue-600 cursor-pointer">
              Show what you can do
            </summary>
            <ul className="mt-2 grid grid-cols-2 gap-1 text-xs text-gray-600">
              {me.permissions.map((permission) => (
                <li key={permission}>
                  <code>{permission}</code>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section className="pt-8 border-t">
        <h3 className="text-lg font-semibold text-gray-900 mb-1">
          Change your password
        </h3>
        <p className="text-sm text-gray-500 mb-4">
          The current password is required, so a stolen session cannot lock you
          out of your own account.
        </p>

        {me?.must_change_password && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 text-sm text-amber-900">
            An administrator reset your password. Please choose your own.
          </div>
        )}

        <form onSubmit={submit} className="space-y-4 max-w-md">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Current password
            </label>
            <input
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              New password
            </label>
            <input
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              required
              minLength={8}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Confirm new password
            </label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <button
            type="submit"
            disabled={changePassword.isPending}
            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            <Lock className="h-4 w-4 mr-2" />
            {changePassword.isPending ? 'Changing…' : 'Change password'}
          </button>
        </form>
      </section>
    </div>
  );
};

export const Settings: React.FC = () => {
  const { user } = useAuth();
  const { can, isLoading } = usePermissions();
  const [activeTab, setActiveTab] = useState<TabId>('profile');

  const visible = TABS.filter((tab) => !tab.permission || can(tab.permission));

  // A permission change can hide the tab someone is looking at.
  const current = visible.some((tab) => tab.id === activeTab)
    ? activeTab
    : 'profile';

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-600">
          Your account, the people who use this installation, and how it behaves.
        </p>
      </div>

      <div className="bg-white rounded-lg shadow">
        <div className="border-b border-gray-200 overflow-x-auto">
          <nav className="flex -mb-px">
            {visible.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`px-6 py-3 text-sm font-medium border-b-2 transition whitespace-nowrap ${
                    current === tab.id
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-600 hover:text-gray-900 hover:border-gray-300'
                  }`}
                >
                  <Icon className="h-4 w-4 inline mr-2" />
                  {tab.label}
                </button>
              );
            })}
          </nav>
        </div>

        <div className="p-6">
          {isLoading ? (
            <div className="py-12 flex justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
            </div>
          ) : (
            <>
              {current === 'profile' && <ProfileTab />}
              {current === 'users' && <UsersTab currentUserId={user?.id} />}
              {current === 'roles' && <RolesTab />}
              {current === 'credentials' && <CredentialsTab />}
              {current === 'application' && <ApplicationTab />}
              {current === 'targets' && <TargetsTab />}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
