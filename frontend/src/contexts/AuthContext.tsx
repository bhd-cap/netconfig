/**
 * Authentication Context
 * Manages user authentication state and provides auth methods
 */
import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'react-hot-toast';
import api from '../lib/api';
import { User, LoginRequest, RegisterRequest, AuthResponse } from '../types';

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (credentials: LoginRequest) => Promise<void>;
  register: (data: RegisterRequest) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();

  // Refresh user data from API
  const refreshUser = useCallback(async () => {
    try {
      const response = await api.get<User>('/auth/me');
      const userData = response.data;

      // Update localStorage and state
      localStorage.setItem('user', JSON.stringify(userData));
      setUser(userData);
    } catch (error) {
      // If refresh fails, logout
      localStorage.removeItem('access_token');
      localStorage.removeItem('user');
      setUser(null);
      throw error;
    }
  }, []); // Empty dependency array to prevent re-creation

  // Initialize auth state from localStorage
  useEffect(() => {
    const initAuth = async () => {
      const token = localStorage.getItem('access_token');
      const savedUser = localStorage.getItem('user');

      if (token && savedUser) {
        try {
          // Parse saved user
          setUser(JSON.parse(savedUser));

          // Verify token is still valid by fetching current user
          await refreshUser();
        } catch (error: any) {
          // Token invalid or expired, clear auth silently
          // This is normal when token expires - don't log as error
          if (error.response?.status !== 401) {
            console.error('Auth initialization failed:', error);
          }
        }
      }

      setIsLoading(false);
    };

    initAuth();
  }, [refreshUser]);

  const login = async (credentials: LoginRequest) => {
    try {
      // Convert to URLSearchParams for OAuth2 password flow (application/x-www-form-urlencoded)
      const formData = new URLSearchParams();
      formData.append('username', credentials.username);
      formData.append('password', credentials.password);

      const response = await api.post<AuthResponse>('/auth/login', formData, {
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
      });

      const { access_token, user: userData } = response.data;

      // Save to localStorage
      localStorage.setItem('access_token', access_token);
      localStorage.setItem('user', JSON.stringify(userData));

      // Update state
      setUser(userData);

      toast.success(`Welcome back, ${userData.username}!`);
      navigate('/');
    } catch (error: any) {
      // Error toast already handled by API interceptor
      // Just rethrow to let component handle it if needed
      throw error;
    }
  };

  const register = async (data: RegisterRequest) => {
    try {
      const response = await api.post<AuthResponse>('/auth/register', data);

      const { access_token, user: userData } = response.data;

      // Save to localStorage
      localStorage.setItem('access_token', access_token);
      localStorage.setItem('user', JSON.stringify(userData));

      // Update state
      setUser(userData);

      toast.success('Registration successful! Welcome!');
      navigate('/');
    } catch (error: any) {
      // Error toast already handled by API interceptor
      // Just rethrow to let component handle it if needed
      throw error;
    }
  };

  const logout = async () => {
    try {
      // Call logout endpoint for audit logging
      await api.post('/auth/logout');
    } catch (error) {
      // Ignore errors, clear local state anyway
    }

    // Clear localStorage
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');

    // Clear state
    setUser(null);

    toast.success('Logged out successfully');
    navigate('/login');
  };

  const value: AuthContextType = {
    user,
    isAuthenticated: !!user,
    isLoading,
    login,
    register,
    logout,
    refreshUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
