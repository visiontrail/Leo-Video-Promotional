const BASE = '';

export interface TaskConfig {
  target_duration_minutes: number;
  speaker_count: number;
  voice_1: string;
  voice_2: string;
  include_character: boolean;
  ai_endpoint?: string;
  ai_model?: string;
  provider_id?: number | null;
}

export interface Provider {
  id: number;
  name: string;
  endpoint: string;
  api_key_masked: string;
  model: string;
  is_default: boolean;
  created_at: string;
}

export interface ProviderInput {
  name: string;
  endpoint: string;
  api_key?: string;
  model: string;
  is_default?: boolean;
}

export interface Task {
  id: string;
  created_at: string;
  updated_at: string;
  source_type: 'youtube' | 'epub' | 'pdf';
  source_url: string | null;
  source_title: string | null;
  status: 'queued' | 'extracting' | 'digesting' | 'tts' | 'composing' | 'complete' | 'failed';
  error_message: string | null;
  config: TaskConfig;
  output_dir: string | null;
  script_path: string | null;
  audio_path: string | null;
  video_path: string | null;
  duration_seconds: number | null;
}

export interface Settings {
  ai_endpoint: string;
  ai_model: string;
  tts_device: string;
  default_voice_1: string;
  default_voice_2: string;
  available_voices: Record<string, { gender: string; lang: string }>;
}

export async function fetchTasks(): Promise<Task[]> {
  const res = await fetch(`${BASE}/api/tasks`);
  const data = await res.json();
  return data.tasks;
}

export async function fetchTask(id: string): Promise<Task> {
  const res = await fetch(`${BASE}/api/tasks/${id}`);
  if (!res.ok) throw new Error('Task not found');
  return res.json();
}

export async function createTask(
  sourceType: string,
  sourceUrl: string | null,
  config: TaskConfig,
  file?: File,
): Promise<Task> {
  const form = new FormData();
  form.append('source_type', sourceType);
  if (sourceUrl) form.append('source_url', sourceUrl);
  form.append('config_json', JSON.stringify(config));
  if (file) form.append('file', file);

  const res = await fetch(`${BASE}/api/tasks`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteTask(id: string): Promise<void> {
  await fetch(`${BASE}/api/tasks/${id}`, { method: 'DELETE' });
}

export async function fetchSettings(): Promise<Settings> {
  const res = await fetch(`${BASE}/api/settings`);
  return res.json();
}

export async function fetchProviders(): Promise<Provider[]> {
  const res = await fetch(`${BASE}/api/providers`);
  const data = await res.json();
  return data.providers;
}

export async function createProvider(input: ProviderInput): Promise<Provider> {
  const res = await fetch(`${BASE}/api/providers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateProvider(id: number, input: Partial<ProviderInput>): Promise<Provider> {
  const res = await fetch(`${BASE}/api/providers/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteProvider(id: number): Promise<void> {
  await fetch(`${BASE}/api/providers/${id}`, { method: 'DELETE' });
}

export function videoUrl(taskId: string): string {
  return `${BASE}/api/tasks/${taskId}/video`;
}

export function audioUrl(taskId: string): string {
  return `${BASE}/api/tasks/${taskId}/audio`;
}

export function scriptUrl(taskId: string): string {
  return `${BASE}/api/tasks/${taskId}/script`;
}
