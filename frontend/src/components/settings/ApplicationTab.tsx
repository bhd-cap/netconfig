/**
 * Application settings tab
 *
 * Backup retention, schedule defaults, email notifications and maintenance
 * windows.
 *
 * The SMTP password is write-only: the API returns only whether one is
 * stored, so leaving the field blank keeps the existing password rather than
 * clearing it.
 */
import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { format } from 'date-fns';
import { toast } from 'react-hot-toast';
import { Clock, Loader2, Mail, Plus, Save, Send, Trash2 } from 'lucide-react';
import api from '../../lib/api';
import { AppSettings, MaintenanceWindow, WEEKDAYS } from '../../types';

const EMPTY_WINDOW: MaintenanceWindow = {
  name: '',
  days: [6],
  start: '22:00',
  end: '02:00',
  suppress_backups: true,
  suppress_notifications: false,
};

export const ApplicationTab: React.FC = () => {
  const queryClient = useQueryClient();

  const { data: settings, isLoading } = useQuery<AppSettings>({
    queryKey: ['app-settings'],
    queryFn: async () => (await api.get('/settings')).data,
  });

  // Local drafts, so a half-typed cron expression is not sent on every
  // keystroke and a validation failure leaves the form as the user left it.
  const [retentionDays, setRetentionDays] = useState('90');
  const [retentionMax, setRetentionMax] = useState('');
  const [retentionEnabled, setRetentionEnabled] = useState(true);

  const [cron, setCron] = useState('0 2 * * *');
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [maxConcurrent, setMaxConcurrent] = useState('10');

  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState('587');
  const [smtpUsername, setSmtpUsername] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');
  const [smtpTls, setSmtpTls] = useState(true);
  const [smtpFrom, setSmtpFrom] = useState('');
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [recipients, setRecipients] = useState('');
  const [onFailure, setOnFailure] = useState(true);
  const [onSuccess, setOnSuccess] = useState(false);
  const [onConfigChange, setOnConfigChange] = useState(true);
  const [onNewHost, setOnNewHost] = useState(false);

  const [timezone, setTimezone] = useState('UTC');
  const [windows, setWindows] = useState<MaintenanceWindow[]>([]);

  useEffect(() => {
    if (!settings) return;

    setRetentionDays(String(settings.retention.retention_days));
    setRetentionMax(
      settings.retention.retention_max_per_device != null
        ? String(settings.retention.retention_max_per_device)
        : ''
    );
    setRetentionEnabled(settings.retention.retention_enabled);

    setCron(settings.schedule.default_schedule_cron);
    setScheduleEnabled(settings.schedule.default_schedule_enabled);
    setMaxConcurrent(String(settings.schedule.max_concurrent_backups));

    setSmtpHost(settings.email.smtp_host ?? '');
    setSmtpPort(String(settings.email.smtp_port));
    setSmtpUsername(settings.email.smtp_username ?? '');
    setSmtpPassword('');
    setSmtpTls(settings.email.smtp_use_tls);
    setSmtpFrom(settings.email.smtp_from_address ?? '');
    setNotificationsEnabled(settings.email.notifications_enabled);
    setRecipients(settings.email.notify_recipients.join(', '));
    setOnFailure(settings.email.notify_on_backup_failure);
    setOnSuccess(settings.email.notify_on_backup_success);
    setOnConfigChange(settings.email.notify_on_config_change);
    setOnNewHost(settings.email.notify_on_new_host);

    setTimezone(settings.maintenance.maintenance_timezone);
    setWindows(settings.maintenance.maintenance_windows);
  }, [settings]);

  const save = useMutation({
    mutationFn: async (payload: Record<string, any>) =>
      (await api.put('/settings', payload)).data as AppSettings,
    onSuccess: (updated) => {
      queryClient.setQueryData(['app-settings'], updated);
      setSmtpPassword('');
      toast.success('Settings saved');
    },
  });

  const sendTest = useMutation({
    mutationFn: async (recipient: string) =>
      (await api.post('/settings/test-email', { recipient })).data,
    onSuccess: (result) => {
      if (result.success) toast.success(result.message);
      else toast.error(result.message);
    },
  });

  const saveRetentionAndSchedule = () => {
    save.mutate({
      retention_days: Number(retentionDays),
      retention_max_per_device: retentionMax ? Number(retentionMax) : undefined,
      clear_retention_max: retentionMax === '',
      retention_enabled: retentionEnabled,
      default_schedule_cron: cron,
      default_schedule_enabled: scheduleEnabled,
      max_concurrent_backups: Number(maxConcurrent),
    });
  };

  const saveEmail = () => {
    const payload: Record<string, any> = {
      smtp_host: smtpHost || null,
      smtp_port: Number(smtpPort),
      smtp_username: smtpUsername || null,
      smtp_use_tls: smtpTls,
      smtp_from_address: smtpFrom || null,
      notifications_enabled: notificationsEnabled,
      notify_recipients: recipients
        .split(',')
        .map((address) => address.trim())
        .filter(Boolean),
      notify_on_backup_failure: onFailure,
      notify_on_backup_success: onSuccess,
      notify_on_config_change: onConfigChange,
      notify_on_new_host: onNewHost,
    };

    // Blank means "leave the stored password alone", not "clear it".
    if (smtpPassword) payload.smtp_password = smtpPassword;

    save.mutate(payload);
  };

  const saveMaintenance = () => {
    save.mutate({
      maintenance_timezone: timezone,
      maintenance_windows: windows,
    });
  };

  const updateWindow = (index: number, patch: Partial<MaintenanceWindow>) => {
    setWindows((current) =>
      current.map((window, position) =>
        position === index ? { ...window, ...patch } : window
      )
    );
  };

  const toggleDay = (index: number, day: number) => {
    setWindows((current) =>
      current.map((window, position) => {
        if (position !== index) return window;
        const days = window.days.includes(day)
          ? window.days.filter((entry) => entry !== day)
          : [...window.days, day].sort();
        return { ...window, days };
      })
    );
  };

  if (isLoading) {
    return (
      <div className="py-12 flex justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
      </div>
    );
  }

  const maintenance = settings?.maintenance;

  return (
    <div className="space-y-8">
      {/* Retention and schedule */}
      <section>
        <h3 className="text-lg font-semibold text-gray-900 mb-1">
          Retention and schedule defaults
        </h3>
        <p className="text-sm text-gray-500 mb-4">
          How long stored configurations are kept, and what a new scheduled job
          starts out as.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-3xl">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Keep backups for (days)
            </label>
            <input
              type="number"
              min={1}
              max={3650}
              value={retentionDays}
              onChange={(event) => setRetentionDays(event.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Max per device
            </label>
            <input
              type="number"
              min={1}
              value={retentionMax}
              onChange={(event) => setRetentionMax(event.target.value)}
              placeholder="no limit"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Concurrent backups
            </label>
            <input
              type="number"
              min={1}
              max={100}
              value={maxConcurrent}
              onChange={(event) => setMaxConcurrent(event.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div className="md:col-span-2">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Default schedule (cron)
            </label>
            <input
              value={cron}
              onChange={(event) => setCron(event.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
            />
            <p className="text-xs text-gray-500 mt-1">
              Five fields: minute hour day-of-month month day-of-week.
            </p>
          </div>

          <div className="flex flex-col justify-center gap-2 text-sm">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={retentionEnabled}
                onChange={(event) => setRetentionEnabled(event.target.checked)}
              />
              Apply retention automatically
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={scheduleEnabled}
                onChange={(event) => setScheduleEnabled(event.target.checked)}
              />
              New jobs start enabled
            </label>
          </div>
        </div>

        <button
          onClick={saveRetentionAndSchedule}
          disabled={save.isPending}
          className="mt-4 inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          <Save className="h-4 w-4 mr-2" />
          Save
        </button>
      </section>

      {/* Email */}
      <section className="pt-8 border-t">
        <h3 className="text-lg font-semibold text-gray-900 mb-1 flex items-center">
          <Mail className="h-5 w-5 mr-2 text-gray-400" />
          Email notifications
        </h3>
        <p className="text-sm text-gray-500 mb-4">
          A mail server problem never fails the backup that triggered the
          notification.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-3xl">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              SMTP server
            </label>
            <input
              value={smtpHost}
              onChange={(event) => setSmtpHost(event.target.value)}
              placeholder="smtp.example.com"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Port
            </label>
            <input
              type="number"
              value={smtpPort}
              onChange={(event) => setSmtpPort(event.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
            <p className="text-xs text-gray-500 mt-1">
              465 is implicit TLS; anything else starts plain and upgrades.
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Username
            </label>
            <input
              value={smtpUsername}
              onChange={(event) => setSmtpUsername(event.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Password
            </label>
            <input
              type="password"
              value={smtpPassword}
              onChange={(event) => setSmtpPassword(event.target.value)}
              placeholder={
                settings?.email.smtp_password_set
                  ? 'stored — leave blank to keep'
                  : 'not set'
              }
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div className="md:col-span-2">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              From address
            </label>
            <input
              value={smtpFrom}
              onChange={(event) => setSmtpFrom(event.target.value)}
              placeholder="backups@example.com"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
          </div>

          <div className="md:col-span-2">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Recipients
            </label>
            <input
              value={recipients}
              onChange={(event) => setRecipients(event.target.value)}
              placeholder="ops@example.com, oncall@example.com"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg"
            />
            <p className="text-xs text-gray-500 mt-1">Comma separated.</p>
          </div>
        </div>

        <div className="flex flex-wrap gap-6 mt-4 text-sm">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={smtpTls}
              onChange={(event) => setSmtpTls(event.target.checked)}
            />
            Use STARTTLS
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={notificationsEnabled}
              onChange={(event) => setNotificationsEnabled(event.target.checked)}
            />
            Send notifications
          </label>
        </div>

        <div className="mt-4">
          <p className="text-sm font-medium text-gray-700 mb-2">Notify me when…</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm max-w-2xl">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={onFailure}
                onChange={(event) => setOnFailure(event.target.checked)}
              />
              A backup fails
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={onSuccess}
                onChange={(event) => setOnSuccess(event.target.checked)}
              />
              A scheduled job completes cleanly
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={onConfigChange}
                onChange={(event) => setOnConfigChange(event.target.checked)}
              />
              A configuration changes
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={onNewHost}
                onChange={(event) => setOnNewHost(event.target.checked)}
              />
              A new host appears on a port
            </label>
          </div>
        </div>

        <div className="flex flex-wrap gap-2 mt-4">
          <button
            onClick={saveEmail}
            disabled={save.isPending}
            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            <Save className="h-4 w-4 mr-2" />
            Save
          </button>

          <button
            onClick={() => {
              const recipient = window.prompt(
                'Send a test message to',
                recipients.split(',')[0]?.trim() ?? ''
              );
              if (recipient?.trim()) sendTest.mutate(recipient.trim());
            }}
            disabled={!settings?.email.smtp_host || sendTest.isPending}
            className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            title={
              settings?.email.smtp_host
                ? 'Send a test message'
                : 'Save an SMTP server first'
            }
          >
            <Send className="h-4 w-4 mr-2" />
            {sendTest.isPending ? 'Sending…' : 'Send test'}
          </button>
        </div>
      </section>

      {/* Maintenance windows */}
      <section className="pt-8 border-t">
        <h3 className="text-lg font-semibold text-gray-900 mb-1 flex items-center">
          <Clock className="h-5 w-5 mr-2 text-gray-400" />
          Maintenance windows
        </h3>
        <p className="text-sm text-gray-500 mb-4">
          A scheduled job that comes due inside a window is held rather than
          run. Times are local to the timezone below and may wrap midnight.
        </p>

        {maintenance && (
          <div
            className={`rounded-lg p-3 mb-4 text-sm ${
              maintenance.currently_open.length
                ? 'bg-amber-50 border border-amber-200 text-amber-900'
                : 'bg-gray-50 border border-gray-200 text-gray-700'
            }`}
          >
            {maintenance.currently_open.length ? (
              <>
                <strong>Open now:</strong> {maintenance.currently_open.join(', ')}
                {maintenance.backups_suppressed && ' — scheduled backups are held'}
              </>
            ) : (
              <>
                No window is open.
                {maintenance.next_window_start && (
                  <>
                    {' '}
                    Next opens{' '}
                    {format(
                      new Date(maintenance.next_window_start),
                      'EEEE d MMM, HH:mm'
                    )}
                    .
                  </>
                )}
              </>
            )}
          </div>
        )}

        <div className="max-w-xs mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Timezone
          </label>
          <input
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            placeholder="UTC"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg"
          />
          <p className="text-xs text-gray-500 mt-1">
            An IANA name, such as Europe/London or America/New_York.
          </p>
        </div>

        <div className="space-y-3">
          {windows.map((window, index) => (
            <div key={index} className="border border-gray-200 rounded-lg p-4">
              <div className="flex flex-wrap items-center gap-3 mb-3">
                <input
                  value={window.name}
                  onChange={(event) =>
                    updateWindow(index, { name: event.target.value })
                  }
                  placeholder="Window name"
                  className="flex-1 min-w-[12rem] px-3 py-2 border border-gray-300 rounded-lg font-medium"
                />

                <label className="text-sm text-gray-600 flex items-center gap-2">
                  from
                  <input
                    type="time"
                    value={window.start}
                    onChange={(event) =>
                      updateWindow(index, { start: event.target.value })
                    }
                    className="px-2 py-1.5 border border-gray-300 rounded"
                  />
                  to
                  <input
                    type="time"
                    value={window.end}
                    onChange={(event) =>
                      updateWindow(index, { end: event.target.value })
                    }
                    className="px-2 py-1.5 border border-gray-300 rounded"
                  />
                </label>

                <button
                  onClick={() =>
                    setWindows((current) =>
                      current.filter((_, position) => position !== index)
                    )
                  }
                  className="text-gray-400 hover:text-red-600"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>

              <div className="flex flex-wrap gap-1 mb-3">
                {WEEKDAYS.map((label, day) => (
                  <button
                    key={label}
                    onClick={() => toggleDay(index, day)}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition ${
                      window.days.includes(day)
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                    }`}
                  >
                    {label.slice(0, 3)}
                  </button>
                ))}
              </div>

              <div className="flex flex-wrap gap-6 text-sm">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={window.suppress_backups}
                    onChange={(event) =>
                      updateWindow(index, { suppress_backups: event.target.checked })
                    }
                  />
                  Hold scheduled backups
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={window.suppress_notifications}
                    onChange={(event) =>
                      updateWindow(index, {
                        suppress_notifications: event.target.checked,
                      })
                    }
                  />
                  Hold notifications
                </label>
              </div>
            </div>
          ))}

          {windows.length === 0 && (
            <p className="text-sm text-gray-500">
              No maintenance windows. Backups run whenever they are scheduled.
            </p>
          )}
        </div>

        <div className="flex gap-2 mt-4">
          <button
            onClick={() => setWindows((current) => [...current, { ...EMPTY_WINDOW }])}
            className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            <Plus className="h-4 w-4 mr-2" />
            Add window
          </button>

          <button
            onClick={saveMaintenance}
            disabled={save.isPending}
            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            <Save className="h-4 w-4 mr-2" />
            Save windows
          </button>
        </div>
      </section>
    </div>
  );
};
