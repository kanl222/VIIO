export interface User {
  id: string;
  username: string;
  role?: string;
  departments?: string[];
}

export interface Conversation {
  id: string;
  user_id?: string;
  title: string;
  agent_thread_id?: string;
  created_at: string;
  updated_at: string;
}

export interface MessageAttachment {
  name?: string;
  type?: string;
  url?: string;
  data?: any;
}

export interface Message {
  id: string;
  conversation_id: string;
  sender: 'user' | 'assistant' | 'system';
  text: string;
  attachments?: MessageAttachment[];
  created_at: string;
}

export interface ChatResponse {
  reply: string;
  thread_id: string;
  message_id: string;
}

export interface ApiError {
  detail?: string;
  error?: string;
  message?: string;
}
