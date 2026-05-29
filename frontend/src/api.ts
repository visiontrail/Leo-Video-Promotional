const BASE = '';

export interface TaskConfig {
  target_duration_minutes: number;
  speaker_count: number;
  voice_1: string;
  voice_2: string;
  include_character: boolean;
  ai_endpoint?: string;
  ai_model?: string;
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

export function videoUrl(taskId: string): string {
  return `${BASE}/api/tasks/${taskId}/video`;
}

export function audioUrl(taskId: string): string {
  return `${BASE}/api/tasks/${taskId}/audio`;
}

export function scriptUrl(taskId: string): string {
  return `${BASE}/api/tasks/${taskId}/script`;
}
