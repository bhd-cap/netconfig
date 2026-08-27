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
export interface Device extends DeviceSnmpFields {
  id: number;
  hostname: string;
  ip_address: string;
  device_type: string;
  port: number;
  // Null when the device logs in with a vault credential instead of its own.
  username: string | null;
  is_active: boolean;
  enable_secret?: string;
  ssh_key_path?: string;
  tags?: Record<string, any>;
  last_backup_at?: string;
  last_backup_status?: string;
  organization_id: number;
  location?: string | null;
  description?: string | null;
  // Set when a discovery crawl registered the device rather than a person.
  discovered?: boolean;
  discovery_source?: string | null;
  last_discovered_at?: string | null;
  // Whether a CLI login has actually succeeded. A device is only worth
  // scheduling for backup when it has.
  last_auth_status?: AuthStatus;
  last_auth_at?: string | null;
  auth_error?: string | null;
  model?: string | null;
  serial_number?: string | null;
  os_version?: string | null;
  // The vault entries this device uses, if any: one for the CLI login, one
  // for SNMP, because a device commonly needs both at once. The names are
  // filled in by the API so a row can say which without a second request.
  credential_id?: number | null;
  credential_name?: string | null;
  snmp_credential_id?: number | null;
  snmp_credential_name?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeviceCreate extends DeviceSnmpFields {
  hostname: string;
  ip_address: string;
  device_type: string;
  // Either these, or a vault credential_id: the API refuses a device with
  // neither rather than creating one nothing can log into.
  username?: string | null;
  password?: string | null;
  port?: number;
  enable_secret?: string;
  ssh_key_path?: string;
  tags?: Record<string, any>;
  is_active?: boolean;
  credential_id?: number | null;
  snmp_credential_id?: number | null;
}

export interface DeviceUpdate extends DeviceSnmpFields {
  hostname?: string;
  ip_address?: string;
  device_type?: string;
  username?: string | null;
  password?: string | null;
  port?: number;
  enable_secret?: string;
  is_active?: boolean;
  tags?: Record<string, any>;
  // Null clears the reference and returns the device to its own credentials;
  // leaving the key out keeps whatever it has.
  credential_id?: number | null;
  snmp_credential_id?: number | null;
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
// Which devices a scheduled job covers. Every criterion is ANDed; a list
// within one criterion is ORed. An empty filter means every device that can be
// backed up, which is what a job created before filtering existed does.
export interface DeviceFilter {
  device_ids?: number[];
  exclude_device_ids?: number[];
  device_types?: string[];
  locations?: string[];
  hostname_pattern?: string;
  tags?: Record<string, string | number | boolean>;
  transports?: string[];
  include_inactive?: boolean;
  // SNMP cannot retrieve a configuration, so those devices are excluded
  // unless this is set.
  include_snmp?: boolean;
}

export interface FilterOptions {
  device_types: string[];
  locations: string[];
  tag_keys: string[];
  transports: string[];
  filter_keys: string[];
}

export interface DeviceFilterPreview {
  total: number;
  summary: string;
  truncated: boolean;
  devices: Array<{
    id: number;
    hostname: string;
    ip_address: string;
    device_type: string;
    location?: string | null;
    transport: string;
    is_active: boolean;
  }>;
  job_id?: number;
  job_name?: string;
}

export interface BackupJob {
  id: number;
  name: string;
  description?: string;
  schedule_cron: string;
  is_enabled: boolean;
  device_filter?: DeviceFilter;
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
  device_filter?: DeviceFilter;
}

export interface BackupJobUpdate {
  name?: string;
  description?: string;
  schedule_cron?: string;
  is_enabled?: boolean;
  // null clears the filter back to "every device".
  device_filter?: DeviceFilter | null;
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

// ---------------------------------------------------------------------------
// Transports
// ---------------------------------------------------------------------------

export const TRANSPORTS = {
  ssh: 'SSH',
  telnet: 'Telnet',
  snmp: 'SNMP (read-only)',
} as const;

export type Transport = keyof typeof TRANSPORTS;

export interface DeviceSnmpFields {
  transport?: Transport;
  snmp_version?: '1' | '2c' | '3' | null;
  snmp_port?: number;
  snmp_v3_user?: string | null;
  snmp_v3_auth_protocol?: string | null;
  snmp_v3_priv_protocol?: string | null;
  // Write-only: sent when set, never returned by a read.
  snmp_community?: string;
  snmp_v3_auth_key?: string;
  snmp_v3_priv_key?: string;
}

// ---------------------------------------------------------------------------
// Discovery and topology
// ---------------------------------------------------------------------------

export interface DiscoveryRun {
  id: number;
  status: string;
  seed_device_id?: number | null;
  max_hops: number;
  devices_probed: number;
  neighbors_found: number;
  hosts_found: number;
  devices_created: number;
  started_at: string;
  finished_at?: string | null;
  duration?: number | null;
  error_message?: string | null;
  details?: Record<string, any> | null;
}

export interface DiscoveryRequest {
  seed_device_id: number;
  max_hops?: number;
  auto_add?: boolean;
  collect_inventory?: boolean;
  run_async?: boolean;
}

export interface Neighbor {
  id: number;
  device_id: number;
  device_hostname?: string;
  local_interface: string;
  remote_hostname: string;
  remote_interface?: string | null;
  remote_platform?: string | null;
  remote_mgmt_ip?: string | null;
  remote_device_id?: number | null;
  capabilities?: string | null;
  protocol: string;
  first_seen: string;
  last_seen: string;
  is_active: boolean;
}

export interface TopologyNode {
  key: string;
  // Which layer of the network this sits in. The default view returns
  // infrastructure only; hosts appear on drill-down.
  tier?: Tier;
  // How many end hosts hang off this node, so a drill-down can be offered
  // without having fetched them.
  host_count?: number;
  infrastructure_links?: number;
  capabilities?: string | null;
  id: number | null;
  label: string;
  type: 'device' | 'unmanaged';
  device_type?: string | null;
  ip_address?: string | null;
  platform?: string | null;
  managed: boolean;
  is_active: boolean;
  discovered: boolean;
  last_backup_status?: string | null;
  link_count: number;
  x?: number;
  y?: number;
  hidden?: boolean;
  icon?: string;
  group?: string;
  notes?: string;
}

export interface TopologyLink {
  key: string;
  source: string;
  target: string;
  source_interface?: string | null;
  target_interface?: string | null;
  protocol: string;
  is_active: boolean;
  last_seen?: string | null;
  confirmed_both_ends: boolean;
  manual: boolean;
  label?: string | null;
  hidden?: boolean;
}

export interface TopologyGraph {
  nodes: TopologyNode[];
  links: TopologyLink[];
  stats: {
    nodes: number;
    managed_nodes: number;
    unmanaged_nodes: number;
    links: number;
    isolated_nodes: number;
    manual_links?: number;
    hidden_hosts?: number;
    total_hosts?: number;
    tiers?: string[];
    by_tier?: Record<string, number>;
  };
  diagram?: { id: number; name: string };
}

export interface DiagramLayout {
  nodes?: Record<string, Partial<TopologyNode>>;
  links?: Array<{
    source: string;
    target: string;
    source_interface?: string | null;
    target_interface?: string | null;
    label?: string | null;
  }>;
  hidden_links?: string[];
  viewport?: Record<string, any>;
}

export interface TopologyDiagram {
  id: number;
  name: string;
  description?: string | null;
  layout: DiagramLayout;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Host inventory, OUI and reports
// ---------------------------------------------------------------------------

export interface HostInventoryEntry {
  id: number;
  // Null once the switch is deleted; the row survives as history.
  device_id?: number | null;
  device_hostname?: string;
  interface: string;
  mac_address: string;
  vlan?: number | null;
  entry_type?: string | null;
  ip_address?: string | null;
  // Entered by a person.
  hostname?: string | null;
  // Announced by the host over LLDP or CDP on this port.
  discovered_hostname?: string | null;
  discovered_via?: string | null;
  discovered_platform?: string | null;
  vendor?: string | null;
  first_seen: string;
  last_seen: string;
  is_active: boolean;
  notes?: string | null;
}

export interface OuiStatus {
  prefixes: number;
  system_file?: string | null;
  ieee_url: string;
  // Every URL a "download registry" import will try, in order.
  ieee_sources?: string[];
  sources: string[];
  note: string;
}

export interface InventorySummary {
  total_entries: number;
  active_entries: number;
  unique_macs: number;
  switches_reporting: number;
  seen_last_24h: number;
  new_last_24h: number;
  with_ip_address: number;
  unknown_vendor: number;
}

export interface VendorReport {
  vendors: Array<{ vendor: string; hosts: number }>;
  total_vendors: number;
}

export interface PortReport {
  ports: Array<{
    device_id: number;
    device_hostname: string;
    interface: string;
    hosts: number;
    first_seen?: string | null;
    last_seen?: string | null;
    likely_uplink: boolean;
  }>;
  total_ports: number;
}

export interface ChangeReportEntry {
  mac_address: string;
  vendor?: string | null;
  ip_address?: string | null;
  device_hostname: string;
  interface: string;
  first_seen?: string;
  last_seen?: string;
}

export interface ChangeReport {
  period_days: number;
  appeared: ChangeReportEntry[];
  disappeared: ChangeReportEntry[];
  appeared_count: number;
  disappeared_count: number;
}

// ---------------------------------------------------------------------------
// Users, roles and permissions
// ---------------------------------------------------------------------------

export interface RoleSummary {
  id: number;
  name: string;
  is_system: boolean;
}

export interface Role {
  id: number;
  name: string;
  description?: string | null;
  permissions: string[];
  is_system: boolean;
  user_count: number;
  created_at: string;
  updated_at?: string | null;
}

export interface AdminUser {
  id: number;
  organization_id: number;
  username: string;
  email: string;
  full_name?: string | null;
  is_active: boolean;
  is_admin: boolean;
  is_superuser: boolean;
  must_change_password: boolean;
  role_id?: number | null;
  role?: RoleSummary | null;
  last_login_at?: string | null;
  deactivated_at?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface PermissionEntry {
  permission: string;
  resource: string;
  action: string;
  description: string;
}

export interface Me {
  id: number;
  organization_id: number;
  username: string;
  email: string;
  full_name?: string | null;
  is_active: boolean;
  is_admin: boolean;
  is_superuser: boolean;
  must_change_password: boolean;
  role?: RoleSummary | null;
  permissions: string[];
}

// ---------------------------------------------------------------------------
// Application settings and remote backup targets
// ---------------------------------------------------------------------------

export interface MaintenanceWindow {
  name: string;
  days: number[];
  start: string;
  end: string;
  suppress_backups: boolean;
  suppress_notifications: boolean;
}

export interface AppSettings {
  organization_id: number;
  retention: {
    retention_days: number;
    retention_max_per_device?: number | null;
    retention_enabled: boolean;
  };
  schedule: {
    default_schedule_cron: string;
    default_schedule_enabled: boolean;
    max_concurrent_backups: number;
  };
  email: {
    smtp_host?: string | null;
    smtp_port: number;
    smtp_username?: string | null;
    smtp_password_set: boolean;
    smtp_use_tls: boolean;
    smtp_from_address?: string | null;
    notifications_enabled: boolean;
    notify_recipients: string[];
    notify_on_backup_failure: boolean;
    notify_on_backup_success: boolean;
    notify_on_config_change: boolean;
    notify_on_new_host: boolean;
  };
  maintenance: {
    maintenance_timezone: string;
    maintenance_windows: MaintenanceWindow[];
    currently_open: string[];
    backups_suppressed: boolean;
    notifications_suppressed: boolean;
    next_window_start?: string | null;
  };
  updated_at?: string | null;
}

export interface BackupTarget {
  id: number;
  name: string;
  protocol: 'sftp' | 'ftp' | 'ftps';
  host: string;
  port: number;
  username: string;
  remote_path: string;
  use_device_subdirectories: boolean;
  is_enabled: boolean;
  upload_on_backup: boolean;
  verify_host_key: boolean;
  has_password: boolean;
  has_private_key: boolean;
  last_status?: string | null;
  last_run_at?: string | null;
  last_error?: string | null;
  uploads_succeeded: number;
  uploads_failed: number;
  created_at: string;
  updated_at?: string | null;
}

export const WEEKDAYS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
] as const;

// ---------------------------------------------------------------------------
// Credential vault
// ---------------------------------------------------------------------------

export type CredentialKind = 'cli' | 'snmp';

export interface Credential {
  id: number;
  name: string;
  description?: string | null;
  kind: CredentialKind;
  priority: number;
  is_enabled: boolean;

  username?: string | null;
  ssh_key_path?: string | null;

  snmp_version?: '1' | '2c' | '3' | null;
  snmp_v3_user?: string | null;
  snmp_v3_auth_protocol?: string | null;
  snmp_v3_priv_protocol?: string | null;

  // Secrets are write-only: a read says only whether one is stored.
  has_password: boolean;
  has_enable_secret: boolean;
  has_community: boolean;
  has_v3_auth_key: boolean;
  has_v3_priv_key: boolean;

  success_count: number;
  failure_count: number;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface CredentialSummary {
  cli: number;
  snmp: number;
  disabled: number;
  total: number;
}

export interface CredentialTestResult {
  success: boolean;
  credential: string;
  device: string;
  transport: string;
  result: string;
  message: string;
  duration_ms: number;
  facts: Record<string, any>;
}

// ---------------------------------------------------------------------------
// Topology tiers
// ---------------------------------------------------------------------------

export type Tier = 'core' | 'distribution' | 'access' | 'edge' | 'host';

export const INFRASTRUCTURE_TIERS: Tier[] = [
  'core',
  'distribution',
  'access',
  'edge',
];

export const ALL_TIERS: Tier[] = [...INFRASTRUCTURE_TIERS, 'host'];

// ---------------------------------------------------------------------------
// Device detail
// ---------------------------------------------------------------------------

export type AuthStatus = 'never' | 'success' | 'auth_failed' | 'unreachable' | 'error';

export interface DeviceProbeResult {
  transport: string;
  result: string;
  credential_name?: string | null;
  attempts: number;
  message?: string | null;
  duration_ms?: number | null;
  probed_at: string;
}

export interface DeviceDetail {
  device: {
    id: number;
    hostname: string;
    ip_address: string;
    device_type: string;
    transport: string;
    port: number;
    username: string;
    location?: string | null;
    description?: string | null;
    tags?: Record<string, any> | null;
    is_active: boolean;
    discovered: boolean;
    discovery_source?: string | null;
    last_discovered_at?: string | null;
    last_backup_at?: string | null;
    last_backup_status?: string | null;
    created_at: string;
  };
  authentication: {
    status: AuthStatus;
    at?: string | null;
    error?: string | null;
    credential_id?: number | null;
    credential_name?: string | null;
    snmp_credential_id?: number | null;
    snmp_credential_name?: string | null;
    backup_eligible: boolean;
  };
  facts: {
    model?: string | null;
    serial_number?: string | null;
    os_version?: string | null;
    snmp_sysname?: string | null;
    snmp_sysdescr?: string | null;
    snmp_location?: string | null;
    snmp_contact?: string | null;
    snmp_uptime_seconds?: number | null;
    snmp_last_polled_at?: string | null;
    extra: Record<string, any>;
  };
  probes: DeviceProbeResult[];
  neighbors: Array<{
    local_interface: string;
    remote_hostname: string;
    remote_interface?: string | null;
    remote_platform?: string | null;
    remote_mgmt_ip?: string | null;
    remote_device_id?: number | null;
    protocol: string;
    is_active: boolean;
    last_seen: string;
  }>;
  hosts: {
    total: number;
    active: number;
    ports_in_use: number;
  };
}

export interface BulkDeviceUpdate {
  device_ids: number[];
  is_active?: boolean;
  device_type?: string;
  transport?: Transport;
  port?: number;
  location?: string;
  description?: string;
  tags?: Record<string, any>;
}
