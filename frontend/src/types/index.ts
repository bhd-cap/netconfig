/**
 * TypeScript type definitions for API entities
 */

// User & Authentication
export interface User {
  id: number;
  username: string;
  email: string;
  full_name?: string;
  organization_id: number;
  is_active: boolean;
  is_admin: boolean;
  is_superuser: boolean;
  created_at: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RegisterRequest {
  username: string;
  email: string;
  password: string;
  full_name?: string;
  organization_name: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

// Device
export interface Device {
  id: number;
  hostname: string;
  ip_address: string;
  device_type: string;
  port: number;
  username: string;
  is_active: boolean;
  enable_secret?: string;
  ssh_key_path?: string;
  tags?: Record<string, any>;
  last_backup_at?: string;
  last_backup_status?: string;
  organization_id: number;
  created_at: string;
  updated_at: string;
}

export interface DeviceCreate {
  hostname: string;
  ip_address: string;
  device_type: string;
  username: string;
  password: string;
  port?: number;
  enable_secret?: string;
  ssh_key_path?: string;
  tags?: Record<string, any>;
  is_active?: boolean;
}

export interface DeviceUpdate {
  hostname?: string;
  ip_address?: string;
  device_type?: string;
  username?: string;
  password?: string;
  port?: number;
  enable_secret?: string;
  is_active?: boolean;
  tags?: Record<string, any>;
}

export interface ConnectivityTestResult {
  success: boolean;
  message: string;
  hostname: string;
  device_info?: {
    device_type: string;
    prompt?: string;
  };
  duration: number;
}

// Configuration/Backup
export interface Configuration {
  id: number;
  device_id: number;
  device_hostname?: string;
  device_ip?: string;
  filename: string;
  file_path: string;
  file_size: number;
  checksum: string;
  config_hash: string;
  backed_up_at: string;
  backup_duration: number;
  status: string;
  error_message?: string;
  created_at: string;
}

export interface BackupTriggerRequest {
  device_ids: number[];
}

export interface BackupTriggerResponse {
  task_id: string;
  device_count: number;
  message: string;
}

export interface TaskStatus {
  task_id: string;
  status: string;
  result?: any;
  error?: string;
  progress?: number;
}

// Backup Job
export interface BackupJob {
  id: number;
  name: string;
  description?: string;
  schedule_cron: string;
  is_enabled: boolean;
  device_filter?: Record<string, any>;
  organization_id: number;
  created_by?: number;
  last_run_at?: string;
  next_run_at?: string;
  created_at: string;
  updated_at: string;
}

export interface BackupJobCreate {
  name: string;
  description?: string;
  schedule_cron: string;
  is_enabled?: boolean;
  device_filter?: Record<string, any>;
}

export interface BackupJobUpdate {
  name?: string;
  description?: string;
  schedule_cron?: string;
  is_enabled?: boolean;
  device_filter?: Record<string, any>;
}

// Configuration Comparison
export interface CompareRequest {
  config1_id: number;
  config2_id: number;
  context_lines?: number;
  include_html?: boolean;
}

export interface CompareResponse {
  is_identical: boolean;
  unified_diff: string;
  html_diff?: string;
  structured_diff: DiffBlock[];
  statistics: {
    added_lines: number;
    removed_lines: number;
    changed_sections: number;
    total_changes: number;
  };
  config1: {
    path: string;
    label?: string;
    line_count: number;
  };
  config2: {
    path: string;
    label?: string;
    line_count: number;
  };
}

export interface DiffBlock {
  type: 'replace' | 'delete' | 'insert';
  old_start: number;
  old_end: number;
  new_start: number;
  new_end: number;
  old_lines: string[];
  new_lines: string[];
}

// Statistics
export interface DashboardStats {
  devices: {
    total: number;
    active: number;
    inactive: number;
    by_type: Record<string, number>;
  };
  backups: {
    total: number;
    successful: number;
    failed: number;
    success_rate: number;
    last_24h: {
      successful: number;
      failed: number;
      total: number;
    };
  };
  jobs: {
    total: number;
    enabled: number;
    disabled: number;
  };
  storage: {
    total_bytes: number;
    total_mb: number;
    total_gb: number;
    avg_backup_bytes: number;
    avg_backup_mb: number;
  };
  recent_activity: {
    items: RecentActivity[];
    count: number;
  };
}

export interface RecentActivity {
  config_id: number;
  device_id: number;
  device_hostname?: string;
  backed_up_at: string;
  status: string;
  file_size: number;
  duration: number;
}

export interface BackupTrend {
  date: string;
  total: number;
  successful: number;
  failed: number;
}

export interface DeviceHealthStatus {
  device_id: number;
  hostname: string;
  status: 'healthy' | 'warning' | 'critical' | 'unknown';
  last_backup_at?: string;
  last_backup_status?: string;
}

// Pagination
export interface PaginatedResponse<T> {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  items: T[];
}

// Common
export interface ApiError {
  detail: string;
}

// Device Types
export const DEVICE_TYPES = {
  cisco_ios: 'Cisco IOS',
  cisco_ios_xe: 'Cisco IOS-XE',
  cisco_nxos: 'Cisco NX-OS',
  arista_eos: 'Arista EOS',
  fortinet: 'Fortinet FortiOS',
  juniper_junos: 'Juniper JunOS',
  aruba_os: 'Aruba ArubaOS',
  hp_comware: 'HPE Comware',
  hp_procurve: 'HPE ProCurve',
} as const;

export type DeviceType = keyof typeof DEVICE_TYPES;
