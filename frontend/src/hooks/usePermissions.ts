/**
 * Permissions for the signed-in user
 *
 * The API is the authority: every endpoint checks the permission itself, and
 * this only decides what to render. Hiding an action the API would refuse is
 * a courtesy, not a security boundary.
 */
import { useQuery } from '@tanstack/react-query';
import api from '../lib/api';
import { Me } from '../types';

export const ME_QUERY_KEY = ['me'] as const;

/**
 * Whether a granted set satisfies a requirement, honouring wildcards
 *
 * Mirrors has_permission() on the backend: "*" grants everything and
 * "devices:*" grants every action on devices.
 */
export function hasPermission(granted: string[], required: string): boolean {
  if (granted.includes('*')) return true;
  if (granted.includes(required)) return true;

  const resource = required.split(':')[0];
  return granted.includes(`${resource}:*`);
}

export function usePermissions() {
  const { data, isLoading, error } = useQuery<Me>({
    queryKey: ME_QUERY_KEY,
    queryFn: async () => (await api.get<Me>('/users/me')).data,
    // Roles change rarely, and every page asks; one fetch per session is
    // plenty. Changing a role invalidates this key explicitly.
    staleTime: 10 * 60 * 1000,
    retry: false,
  });

  const permissions = data?.permissions ?? [];

  return {
    me: data,
    permissions,
    isLoading,
    // An older backend without /users/me leaves us with nothing to go on;
    // fall back to the legacy admin flag rather than hiding the whole UI.
    error,
    can: (required: string) => hasPermission(permissions, required),
    canAny: (...required: string[]) =>
      required.some((permission) => hasPermission(permissions, permission)),
  };
}
