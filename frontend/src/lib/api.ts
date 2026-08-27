/**
 * API Client Configuration
 * Handles all HTTP requests to the backend API
 */
import axios, { AxiosError } from 'axios';
import { toast } from 'react-hot-toast';

// Get API URL from environment or use default
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

// Create axios instance
export const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor - add auth token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor - handle errors
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<any>) => {
    // Handle 401 Unauthorized - token expired or invalid
    if (error.response?.status === 401) {
      // Don't redirect if this is an auth check (refreshUser call)
      const isAuthCheck = error.config?.url?.includes('/auth/me');

      if (!isAuthCheck) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('user');
        window.location.href = '/login';
        toast.error('Session expired. Please login again.');
      }
    }

    // Extract error message safely
    let message = 'An error occurred';

    if (error.response?.data) {
      const data = error.response.data;

      // Handle FastAPI validation errors (array of objects)
      if (Array.isArray(data.detail)) {
        message = data.detail.map((err: any) => err.msg || String(err)).join(', ');
      }
      // Handle standard detail string
      else if (typeof data.detail === 'string') {
        message = data.detail;
      }
      // Handle plain string response
      else if (typeof data === 'string') {
        message = data;
      }
    }

    // Don't show toast for certain errors (handled by component)
    if (error.config?.headers?.['X-No-Toast'] !== 'true') {
      // Also don't show toast for auth check failures
      const isAuthCheck = error.config?.url?.includes('/auth/me');
      if (!isAuthCheck) {
        toast.error(message);
      }
    }

    return Promise.reject(error);
  }
);

export default api;
