import { useEffect, useState } from 'react';
import { useStore } from '../store';
import { api } from '../api';
import type { StreamInfo, SystemLogsData } from '../api';

export default function ModelConfig() {
  const agentConfig = useStore((s) => s.agentConfig);
  const changeLog = useStore((s) => s.changeLog);
  const loadAgentConfig = useStore((s) => s.loadAgentConfig);
  const toast = useStore((s) => s.toast);

  const [selMap, setSelMap] = useState<Record<string, string>>({});
  const [statusMap, setStatusMap] = useState<Record<string, { cls: string; text: string }>>({});
  const [sysLogs, setSysLogs] = useState<SystemLogsData | null>(null);
  const [sysOpen, setSysOpen] = useState(false);

  useEffect(() => {
    loadAgentConfig();
    api.systemLogs().then(setSysLogs).catch(() => {});
  }, [loadAgentConfig]);

  useEffect(() => {
    if (agentConfig?.agents) {
      const m: Record<string, string> = {};
      agentConfig.agents.forEach((ag) => {
        m[ag.id] = ag.model;
      });
      setSelMap(m);
    }
  }, [agentConfig]);

  if (!agentConfig?.agents) {
    return <div className="empty" style={{ gridColumn: '1/-1' }}>⚠️ 请先启动本地服务器</div>;
  }

  const models = agentConfig.knownModels?.length
    ? agentConfig.knownModels.map((m) => ({ id: m.id, l: m.label, p: m.provider }))
    : agentConfig.agents.map((ag) => ({ id: ag.model, l: ag.model, p: 'Current' }))
        .filter((model, index, list) => list.findIndex((item) => item.id === model.id) === index);

  const handleSelect = (agentId: string, val: string) => {
    setSelMap((p) => ({ ...p, [agentId]: val }));
  };

  const resetMC = (agentId: string) => {
    const ag = agentConfig.agents.find((a) => a.id === agentId);
    if (ag) setSelMap((p) => ({ ...p, [agentId]: ag.model }));
  };

  const applyModel = async (agentId: string) => {
    const model = selMap[agentId];
    if (!model) return;
    setStatusMap((p) => ({ ...p, [agentId]: { cls: 'pending', text: '⟳ 提交中…' } }));
    try {
      const r = await api.setModel(agentId, model);
      if (r.ok) {
        setStatusMap((p) => ({ ...p, [agentId]: { cls: 'ok', text: '✅ 已提交，Gateway 重启中（约5秒）' } }));
        toast(agentId + ' 模型已更改', 'ok');
        setTimeout(() => loadAgentConfig(), 5500);
      } else {
        setStatusMap((p) => ({ ...p, [agentId]: { cls: 'err', text: '❌ ' + (r.error || '错误') } }));
      }
    } catch {
      setStatusMap((p) => ({ ...p, [agentId]: { cls: 'err', text: '❌ 无法连接服务器' } }));
    }
  };

  return (
    <div>
      <div className="model-grid">
        {agentConfig.agents.map((ag) => {
          const sel = selMap[ag.id] || ag.model;
          const changed = sel !== ag.model;
          const st = statusMap[ag.id];
          return (
            <div className="mc-card" key={ag.id}>
              <div className="mc-top">
                <span className="mc-emoji">{ag.emoji || '🏛️'}</span>
                <div>
                  <div className="mc-name">
                    {ag.label}{' '}
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>{ag.id}</span>
                  </div>
                  <div className="mc-role">{ag.role}</div>
                </div>
              </div>
              <div className="mc-cur">
                当前: <b>{ag.model}</b>
              </div>
              <select className="msel" value={sel} onChange={(e) => handleSelect(ag.id, e.target.value)}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.l} ({m.p})
                  </option>
                ))}
              </select>
              <div className="mc-btns">
                <button className="btn btn-p" disabled={!changed} onClick={() => applyModel(ag.id)}>
                  应用
                </button>
                <button className="btn btn-g" onClick={() => resetMC(ag.id)}>
                  重置
                </button>
              </div>
              {st && <div className={`mc-st ${st.cls}`}>{st.text}</div>}
            </div>
          );
        })}
      </div>

      {/* Change Log */}
      <div style={{ marginTop: 24 }}>
        <div className="sec-title">变更日志</div>
        <div className="cl-list">
          {!changeLog?.length ? (
            <div style={{ fontSize: 12, color: 'var(--muted)', padding: '8px 0' }}>暂无变更</div>
          ) : (
            [...changeLog]
              .reverse()
              .slice(0, 15)
              .map((e, i) => (
                <div className="cl-row" key={i}>
                  <span className="cl-t">{(e.at || '').substring(0, 16).replace('T', ' ')}</span>
                  <span className="cl-a">{e.agentId}</span>
                  <span className="cl-c">
                    <b>{e.oldModel}</b> → <b>{e.newModel}</b>
                    {e.rolledBack && (
                      <span
                        style={{
                          color: 'var(--danger)',
                          fontSize: 10,
                          border: '1px solid #ff527044',
                          padding: '1px 5px',
                          borderRadius: 3,
                          marginLeft: 4,
                        }}
                      >
                        ⚠ 已回滚
                      </span>
                    )}
                  </span>
                </div>
              ))
          )}
        </div>
      </div>

      {/* ── 系统状态 ── */}
      <div style={{ marginTop: 24, borderTop: '1px solid var(--line)', paddingTop: 16 }}>
        <div
          style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', userSelect: 'none' }}
          onClick={() => { setSysOpen(!sysOpen); if (!sysOpen) api.systemLogs().then(setSysLogs).catch(() => {}); }}
        >
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--acc)' }}>⚙ 系统状态</span>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>{sysOpen ? '▼' : '▶'}</span>
          {sysLogs && (sysLogs.streams || []).some(s => s.pending > 0) && (
            <span style={{ fontSize: 10, background: 'var(--danger)', color: '#fff', padding: '1px 6px', borderRadius: 8 }}>
              积压
            </span>
          )}
        </div>
        {sysOpen && sysLogs && (
          <div style={{ marginTop: 12, fontSize: 12 }}>
            <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
              <span style={{ padding: '4px 10px', borderRadius: 6, background: sysLogs.gateway?.alive ? 'rgba(76,175,80,.15)' : 'rgba(244,67,54,.15)', color: sysLogs.gateway?.alive ? 'var(--ok)' : 'var(--danger)' }}>
                🌐 通信网关 {sysLogs.gateway?.alive ? '运行中' : '离线'}
              </span>
              <span style={{ padding: '4px 10px', borderRadius: 6, background: 'rgba(106,158,255,.1)', color: 'var(--acc)' }}>
                🏛️ 调度中心 {sysLogs.workers?.orchestrator?.status === 'running' ? '运行中' : '异常'}
              </span>
              <span style={{ padding: '4px 10px', borderRadius: 6, background: 'rgba(106,158,255,.1)', color: 'var(--acc)' }}>
                🚀 派发中心 {sysLogs.workers?.dispatcher?.status === 'running' ? '运行中' : '异常'}
              </span>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--muted)' }}>
                  <th style={{ textAlign: 'left', padding: '4px 8px' }}>事件类型</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px' }}>累计处理</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px' }}>排队中</th>
                  <th style={{ textAlign: 'left', padding: '4px 8px' }}>处理方</th>
                  <th style={{ textAlign: 'center', padding: '4px 8px' }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {(sysLogs.streams || []).map((s: StreamInfo) => {
                  const TOPIC_LABELS: Record<string, string> = {
                    'task.created': '📜 旨意下达',
                    'task.status': '🔄 状态流转',
                    'task.dispatch': '🚀 Agent 派发',
                    'task.completed': '✅ 任务完成',
                    'task.stalled': '⚠️ 停滞检测',
                    'agent.heartbeat': '💓 Agent 心跳',
                  };
                  const GROUP_LABELS: Record<string, string> = {
                    'orchestrator': '调度中心',
                    'dispatcher': '派发中心',
                  };
                  return (
                  <tr key={s.topic} style={{ borderBottom: '1px solid var(--line)' }}>
                    <td style={{ padding: '4px 8px' }}>{TOPIC_LABELS[s.topic] || s.topic}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right' }}>{s.length}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', color: s.pending > 0 ? 'var(--danger)' : 'var(--ok)', fontWeight: s.pending > 0 ? 700 : 400 }}>
                      {s.pending}
                    </td>
                    <td style={{ padding: '4px 8px', fontSize: 10 }}>{GROUP_LABELS[s.consumerGroup] || s.consumerGroup}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'center' }}>
                      {s.pending > 0 && (
                        <button
                          style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, border: '1px solid var(--danger)', background: 'transparent', color: 'var(--danger)', cursor: 'pointer' }}
                          onClick={async () => {
                            if (!confirm(`确认清除 ${s.topic} 的 ${s.pending} 条积压？`)) return;
                            try {
                              await api.flushPending(s.topic, s.consumerGroup);
                              toast('已清除积压', 'ok');
                              api.systemLogs().then(setSysLogs).catch(() => {});
                            } catch { toast('清除失败', 'err'); }
                          }}
                        >清除</button>
                      )}
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
