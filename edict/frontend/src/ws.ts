/**
 * EDICT WebSocket 客户端 — 实时事件推送
 * 自动重连，指数退避，最多 5 次
 */

export interface WSEvent {
  type: string;
  topic?: string;
  data?: unknown;
}

type EventCallback = (event: WSEvent) => void;

function defaultWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws`;
}

class EdictWS {
  private ws: WebSocket | null = null;
  private callbacks: EventCallback[] = [];
  private retries = 0;
  private readonly maxRetries = 5;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;
  private _url = '';

  connect(url?: string): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this._url = url || defaultWsUrl();
    this.intentionalClose = false;
    this.retries = 0;
    this._doConnect();
  }

  private _doConnect(): void {
    try {
      this.ws = new WebSocket(this._url);

      this.ws.onopen = () => {
        this.retries = 0;
      };

      this.ws.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data as string) as WSEvent;
          this.callbacks.forEach((cb) => cb(event));
        } catch {
          // ignore malformed messages
        }
      };

      this.ws.onclose = () => {
        this.ws = null;
        if (!this.intentionalClose && this.retries < this.maxRetries) {
          const delay = Math.min(1000 * Math.pow(2, this.retries), 30000);
          this.retries++;
          this.reconnectTimer = setTimeout(() => this._doConnect(), delay);
        }
      };

      this.ws.onerror = () => {
        this.ws?.close();
      };
    } catch {
      // WebSocket construction failed (e.g. invalid URL in SSR)
    }
  }

  onEvent(callback: EventCallback): void {
    this.callbacks.push(callback);
  }

  offEvent(callback: EventCallback): void {
    this.callbacks = this.callbacks.filter((cb) => cb !== callback);
  }

  disconnect(): void {
    this.intentionalClose = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.retries = 0;
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

export const edictWS = new EdictWS();
