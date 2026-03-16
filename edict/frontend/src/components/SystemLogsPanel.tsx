import { useEffect, useState } from 'react';
import { api } from '../api';

interface StreamInfo {
  topic: string;
  length: number;
  pending: number;
  consumerGroup: string;
}

interface SystemLogsData {
  streams: StreamInfo[];
  gateway: { alive: boolean; status: string };
  workers: { orchestrator: { status: string }; dispatcher: { status: string } };
}

export default function SystemLogsPanel() {
  const [data, setData] = useState<SystemLogsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [flushing, setFlushing] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const resp = await api.systemLogs();
      setData(resp);
    } catch {
      setData(null);
    }
    setLoading(false);
  };

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);

  const handleFlush = async (topic: string, group: string) => {
    setFlushing(topic);
    try {
      await api.flushPending(topic, group);
      await load();
    } catch { /* ignore */ }
    setFlushing(null);
  };

  const totalPending = data?.streams?.reduce((a, s) => a + (s.pending || 0), 0) || 0;
  const flushAll = async () => {
    if (!data?.streams) return;
    setFlushing('all');
    for (const s of data.streams) {
      if (s.pending > 0 && s.consumerGroup) {
        await api.flushPending(s.topic, s.consumerGroup);
      }
    }
    await load();
    setFlushing(null);
  };

  return (
    <div style={{ padding: '20px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ margin: 0, fontSize: 16, color: 'var(--text)' }}>
          系统监控
          {totalPending > 0 && <span style={{ color: 'var(--danger)', marginLeft: 8, fontSize: 13 }}>⚠ {totalPending} pending</span>}
        </h3>
        <div style={{ display: 'flex', gap: 8 }}>
          {totalPending > 0 && (
            <button
              className="btn btn-sm"
              style={{ background: 'var(--danger)', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 12 }}
              onClick={flushAll}
              disabled={flushing !== null}
            >
              {flushing === 'all' ? '清除中...' : `🗑 清除全部积压 (${totalPending})`}
            </button>
          )}
          <button
            className="btn btn-sm"
            style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 12, color: 'var(--text)' }}
            onClick={load}
            disabled={loading}
          >
            {loading ? '⏳' : '🔄 刷新'}
          </button>
        </div>
      </div>

      {/* Workers + Gateway Status */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        {[
          { label: 'Gateway', status: data?.gateway?.alive ? 'running' : 'offline', color: data?.gateway?.alive ? 'var(--ok)' : 'var(--danger)' },
          { label: 'Orchestrator', status: data?.workers?.orchestrator?.status || '?', color: data?.workers?.orchestrator?.status === 'running' ? 'var(--ok)' : 'var(--muted)' },
          { label: 'Dispatcher', status: data?.workers?.dispatcher?.status || '?', color: data?.workers?.dispatcher?.status === 'running' ? 'var(--ok)' : 'var(--muted)' },
        ].map(w => (
          <div key={w.label} style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 16px', flex: 1, textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>{w.label}</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: w.color }}>
              {w.status === 'running' ? '🟢 运行中' : w.status === 'offline' ? '🔴 离线' : '⚪ 未知'}
            </div>
          </div>
        ))}
      </div>

      {/* Redis Streams Table */}
      <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 10, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--line)' }}>
              <th style={{ padding: '10px 14px', textAlign: 'left', color: 'var(--muted)', fontWeight: 500, fontSize: 11 }}>Topic</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', color: 'var(--muted)', fontWeight: 500, fontSize: 11 }}>消息总数</th>
              <th style={{ padding: '10px 14px', textAlign: 'right', color: 'var(--muted)', fontWeight: 500, fontSize: 11 }}>Pending</th>
              <th style={{ padding: '10px 14px', textAlign: 'left', color: 'var(--muted)', fontWeight: 500, fontSize: 11 }}>消费者组</th>
              <th style={{ padding: '10px 14px', textAlign: 'center', color: 'var(--muted)', fontWeight: 500, fontSize: 11 }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {(data?.streams || []).map(s => (
              <tr key={s.topic} style={{ borderBottom: '1px solid var(--line)' }}>
                <td style={{ padding: '8px 14px', fontFamily: 'monospace', fontSize: 12 }}>{s.topic}</td>
                <td style={{ padding: '8px 14px', textAlign: 'right' }}>{s.length}</td>
                <td style={{ padding: '8px 14px', textAlign: 'right', color: s.pending > 0 ? 'var(--danger)' : 'var(--ok)', fontWeight: s.pending > 0 ? 700 : 400 }}>
                  {s.pending > 0 ? `⚠ ${s.pending}` : '0'}
                </td>
                <td style={{ padding: '8px 14px', fontSize: 12, color: 'var(--muted)' }}>{s.consumerGroup}</td>
                <td style={{ padding: '8px 14px', textAlign: 'center' }}>
                  {s.pending > 0 && s.consumerGroup && (
                    <button
                      style={{ background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', fontSize: 11, color: 'var(--text)' }}
                      onClick={() => handleFlush(s.topic, s.consumerGroup)}
                      disabled={flushing !== null}
                    >
                      {flushing === s.topic ? '...' : '清除'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {(!data?.streams || data.streams.length === 0) && (
              <tr><td colSpan={5} style={{ padding: 20, textAlign: 'center', color: 'var(--muted)' }}>
                {loading ? '加载中...' : '无法获取 Redis Streams 数据'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
