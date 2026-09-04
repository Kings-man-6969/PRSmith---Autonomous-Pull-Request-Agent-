import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { RefreshCw, Search, X, ZoomIn, ZoomOut, Maximize2, RotateCcw, ArrowRight, ArrowLeft } from 'lucide-react';
import type { GraphNodeData, GraphEdgeData, RepositoryItem } from '../api/client';
import {
  fetchTrackedRepos, fetchGraphNodes, fetchGraphEdges,
  fetchGraphStatus, triggerGraphBuild,
} from '../api/client';

/* ─── Node type colours — subtle, semantic palette ────────────────────────── */
const NODE_COLORS: Record<string, string> = {
  function:    '#0070f3',  // Link blue
  method:      '#7928ca',  // Violet
  class:       '#171717',  // Primary ink
  module:      '#666666',  // Muted gray
  test:        '#107c41',  // Forest green
  apiendpoint: '#d97706',  // Amber
};

const EDGE_COLORS: Record<string, string> = {
  calls:    '#0070f3',
  imports:  '#888888',
  tests:    '#107c41',
  inherits: '#7928ca',
  defines:  '#d1d5db',
};

const nodeColor = (t: string) => NODE_COLORS[t.toLowerCase()] ?? '#4b5563';
const edgeColor = (t: string) => EDGE_COLORS[t.toLowerCase()] ?? '#9ca3af';

interface LayoutNode extends GraphNodeData {
  x: number;
  y: number;
  degree: number;
}

/* ─── Clustered Organic Layout ────────────────────────────────────────────── */
function computeGraphLayout(nodes: GraphNodeData[], edges: GraphEdgeData[], width: number, height: number): LayoutNode[] {
  if (nodes.length === 0) return [];

  // 1. Calculate degree for each node
  const degreeMap: Record<string, number> = {};
  nodes.forEach(n => { degreeMap[n.id] = 0; });
  edges.forEach(e => {
    if (degreeMap[e.source_node_id] !== undefined) degreeMap[e.source_node_id]++;
    if (degreeMap[e.target_node_id] !== undefined) degreeMap[e.target_node_id]++;
  });

  // 2. Group nodes by directory / module cluster
  const clusterMap: Record<string, GraphNodeData[]> = {};
  nodes.forEach(n => {
    const parts = n.file_path.split('/');
    const dir = parts.length > 1 ? parts.slice(0, -1).join('/') : 'root';
    if (!clusterMap[dir]) clusterMap[dir] = [];
    clusterMap[dir].push(n);
  });

  const clusters = Object.keys(clusterMap);
  const numClusters = clusters.length;
  const cx = width / 2;
  const cy = height / 2;
  const majorRadius = Math.min(width, height) * 0.38;

  const result: LayoutNode[] = [];

  clusters.forEach((clusterName, cIdx) => {
    const clusterNodes = clusterMap[clusterName];
    const clusterAngle = (cIdx / numClusters) * 2 * Math.PI;
    const clusterCenterX = numClusters === 1 ? cx : cx + majorRadius * Math.cos(clusterAngle);
    const clusterCenterY = numClusters === 1 ? cy : cy + majorRadius * Math.sin(clusterAngle);

    const clusterSize = clusterNodes.length;
    const localRadius = Math.min(160, Math.max(45, clusterSize * 16));

    clusterNodes.forEach((node, nIdx) => {
      const localAngle = (nIdx / clusterSize) * 2 * Math.PI;
      const r = clusterSize === 1 ? 0 : localRadius * (0.4 + 0.6 * (nIdx % 2 ? 1 : 0.65));
      result.push({
        ...node,
        x: clusterCenterX + r * Math.cos(localAngle),
        y: clusterCenterY + r * Math.sin(localAngle),
        degree: degreeMap[node.id] || 0,
      });
    });
  });

  return result;
}

/* ─── Production Graph Canvas Component ───────────────────────────────────── */
const GraphCanvas: React.FC<{
  nodes: GraphNodeData[];
  edges: GraphEdgeData[];
  searchTerm: string;
  selectedTypes: Set<string>;
}> = ({ nodes, edges, searchTerm, selectedTypes }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<LayoutNode | null>(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const [isDragging, setIsDragging] = useState(false);

  const dragStartRef = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean }>({
    x: 0, y: 0, tx: 0, ty: 0, moved: false,
  });

  const W = 1200;
  const H = 800;

  // Filter visible nodes
  const visibleNodes = useMemo(() => {
    return nodes.filter(n => {
      if (selectedTypes.size > 0 && !selectedTypes.has(n.node_type.toLowerCase())) return false;
      if (searchTerm && !n.name.toLowerCase().includes(searchTerm.toLowerCase()) && !n.file_path.toLowerCase().includes(searchTerm.toLowerCase())) return false;
      return true;
    });
  }, [nodes, selectedTypes, searchTerm]);

  const visibleIds = useMemo(() => new Set(visibleNodes.map(n => n.id)), [visibleNodes]);

  const visibleEdges = useMemo(() => {
    return edges.filter(e => visibleIds.has(e.source_node_id) && visibleIds.has(e.target_node_id));
  }, [edges, visibleIds]);

  const laidNodes = useMemo(() => {
    return computeGraphLayout(visibleNodes, visibleEdges, W, H);
  }, [visibleNodes, visibleEdges]);

  const nodeMap = useMemo(() => {
    return Object.fromEntries(laidNodes.map(n => [n.id, n]));
  }, [laidNodes]);

  // Highlight connections
  const connectedNodeIds = useMemo(() => {
    const active = hoveredNode || selectedNode;
    if (!active) return null;
    const ids = new Set<string>([active.id]);
    visibleEdges.forEach(e => {
      if (e.source_node_id === active.id) ids.add(e.target_node_id);
      if (e.target_node_id === active.id) ids.add(e.source_node_id);
    });
    return ids;
  }, [hoveredNode, selectedNode, visibleEdges]);

  const connectedEdgeIds = useMemo(() => {
    const active = hoveredNode || selectedNode;
    if (!active) return null;
    return new Set(
      visibleEdges
        .filter(e => e.source_node_id === active.id || e.target_node_id === active.id)
        .map(e => e.id)
    );
  }, [hoveredNode, selectedNode, visibleEdges]);

  // Zoom controls
  const handleZoom = useCallback((factor: number) => {
    setTransform(prev => {
      const nextK = Math.min(4.0, Math.max(0.15, prev.k * factor));
      const cx = W / 2;
      const cy = H / 2;
      return {
        k: nextK,
        x: cx - (cx - prev.x) * (nextK / prev.k),
        y: cy - (cy - prev.y) * (nextK / prev.k),
      };
    });
  }, [W, H]);

  const handleReset = useCallback(() => {
    setTransform({ x: 0, y: 0, k: 1 });
  }, []);

  const handleFit = useCallback(() => {
    if (laidNodes.length === 0) return;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    laidNodes.forEach(n => {
      if (n.x < minX) minX = n.x;
      if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.y > maxY) maxY = n.y;
    });
    const padding = 80;
    const bw = Math.max(100, maxX - minX + padding * 2);
    const bh = Math.max(100, maxY - minY + padding * 2);
    const k = Math.min(2.0, Math.max(0.25, Math.min(W / bw, H / bh)));
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    setTransform({
      k,
      x: W / 2 - centerX * k,
      y: H / 2 - centerY * k,
    });
  }, [laidNodes, W, H]);

  // Mouse pan handlers on container with window release
  const onMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      tx: transform.x,
      ty: transform.y,
      moved: false,
    };
    setIsDragging(true);
  };

  useEffect(() => {
    const onMouseMoveWindow = (e: MouseEvent) => {
      if (!isDragging) return;
      const dx = e.clientX - dragStartRef.current.x;
      const dy = e.clientY - dragStartRef.current.y;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
        dragStartRef.current.moved = true;
      }
      setTransform(t => ({
        ...t,
        x: dragStartRef.current.tx + dx,
        y: dragStartRef.current.ty + dy,
      }));
    };

    const onMouseUpWindow = () => {
      setIsDragging(false);
    };

    if (isDragging) {
      window.addEventListener('mousemove', onMouseMoveWindow);
      window.addEventListener('mouseup', onMouseUpWindow);
    }
    return () => {
      window.removeEventListener('mousemove', onMouseMoveWindow);
      window.removeEventListener('mouseup', onMouseUpWindow);
    };
  }, [isDragging]);

  // Isolated native wheel zoom (prevents window scrolling)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const handleWheelNative = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const factor = e.deltaY < 0 ? 1.12 : 0.89;
      const rect = el.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      setTransform(prev => {
        const nextK = Math.min(4.5, Math.max(0.15, prev.k * factor));
        return {
          k: nextK,
          x: mouseX - (mouseX - prev.x) * (nextK / prev.k),
          y: mouseY - (mouseY - prev.y) * (nextK / prev.k),
        };
      });
    };

    el.addEventListener('wheel', handleWheelNative, { passive: false });
    return () => {
      el.removeEventListener('wheel', handleWheelNative);
    };
  }, []);

  const handleDoubleClick = (e: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    const factor = e.shiftKey ? 0.7 : 1.4;

    setTransform(prev => {
      const nextK = Math.min(4.5, Math.max(0.15, prev.k * factor));
      return {
        k: nextK,
        x: mouseX - (mouseX - prev.x) * (nextK / prev.k),
        y: mouseY - (mouseY - prev.y) * (nextK / prev.k),
      };
    });
  };

  const handleNodeClick = (node: LayoutNode, e: React.MouseEvent) => {
    e.stopPropagation();
    if (dragStartRef.current.moved) return;
    setSelectedNode(prev => (prev?.id === node.id ? null : node));
  };

  return (
    <div style={{ display: 'flex', gap: 'var(--space-md)', height: '100%', position: 'relative' }}>
      {/* Isolated Interactive Canvas Container */}
      <div
        ref={containerRef}
        style={{
          flex: 1,
          background: 'var(--canvas)',
          backgroundImage: 'radial-gradient(var(--hairline) 1px, transparent 1px)',
          backgroundSize: '24px 24px',
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-md)',
          overflow: 'hidden',
          position: 'relative',
          cursor: isDragging ? 'grabbing' : 'grab',
          userSelect: 'none',
        }}
        onMouseDown={onMouseDown}
        onDoubleClick={handleDoubleClick}
      >
        {/* Floating Zoom HUD Controls */}
        <div
          style={{
            position: 'absolute',
            top: 14,
            right: 14,
            zIndex: 10,
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            background: 'var(--canvas-elevated)',
            border: '1px solid var(--hairline)',
            borderRadius: 'var(--radius-sm)',
            padding: '3px 6px',
            boxShadow: 'var(--shadow-sm)',
          }}
        >
          <button className="btn btn-ghost btn-sm" style={{ padding: 4 }} onClick={() => handleZoom(1.25)} title="Zoom In">
            <ZoomIn size={14} />
          </button>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)', padding: '0 4px', minWidth: 38, textAlign: 'center' }}>
            {Math.round(transform.k * 100)}%
          </span>
          <button className="btn btn-ghost btn-sm" style={{ padding: 4 }} onClick={() => handleZoom(0.8)} title="Zoom Out">
            <ZoomOut size={14} />
          </button>
          <div style={{ width: 1, height: 14, background: 'var(--hairline)', margin: '0 2px' }} />
          <button className="btn btn-ghost btn-sm" style={{ padding: 4 }} onClick={handleFit} title="Fit to Graph">
            <Maximize2 size={14} />
          </button>
          <button className="btn btn-ghost btn-sm" style={{ padding: 4 }} onClick={handleReset} title="Reset View (100%)">
            <RotateCcw size={14} />
          </button>
        </div>

        {/* Hover Inspector Tooltip */}
        {hoveredNode && !selectedNode && (
          <div
            style={{
              position: 'absolute',
              bottom: 14,
              left: 14,
              zIndex: 10,
              background: 'var(--canvas-elevated)',
              border: '1px solid var(--hairline)',
              borderRadius: 'var(--radius-sm)',
              padding: '8px 12px',
              boxShadow: 'var(--shadow-sm)',
              pointerEvents: 'none',
              maxWidth: 320,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
              <span
                style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: nodeColor(hoveredNode.node_type),
                  display: 'inline-block',
                }}
              />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>
                {hoveredNode.name}
              </span>
              <span className="badge badge-default" style={{ fontSize: 10 }}>{hoveredNode.node_type}</span>
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {hoveredNode.file_path}:{hoveredNode.start_line}
            </div>
          </div>
        )}

        {/* SVG Drawing Surface */}
        <svg
          width="100%"
          height="100%"
          viewBox={`0 0 ${W} ${H}`}
          style={{ display: 'block', width: '100%', height: '100%' }}
        >
          <defs>
            <marker id="arrow" markerWidth="6" markerHeight="6" refX="8" refY="3" orient="auto">
              <path d="M0,0 L0,6 L6,3 z" fill="#9ca3af" />
            </marker>
            <marker id="arrow-active" markerWidth="6" markerHeight="6" refX="8" refY="3" orient="auto">
              <path d="M0,0 L0,6 L6,3 z" fill="#0070f3" />
            </marker>
          </defs>

          <g transform={`translate(${transform.x}, ${transform.y}) scale(${transform.k})`}>
            {/* Edges */}
            {visibleEdges.map(e => {
              const src = nodeMap[e.source_node_id];
              const tgt = nodeMap[e.target_node_id];
              if (!src || !tgt) return null;

              const isEdgeActive = connectedEdgeIds ? connectedEdgeIds.has(e.id) : false;
              const isDimmed = connectedEdgeIds !== null && !isEdgeActive;

              return (
                <line
                  key={e.id}
                  x1={src.x}
                  y1={src.y}
                  x2={tgt.x}
                  y2={tgt.y}
                  stroke={isEdgeActive ? '#0070f3' : edgeColor(e.edge_type)}
                  strokeWidth={isEdgeActive ? 2.0 : 1.0}
                  strokeDasharray={isEdgeActive ? '4 2' : undefined}
                  markerEnd={isEdgeActive ? 'url(#arrow-active)' : 'url(#arrow)'}
                  opacity={isDimmed ? 0.15 : isEdgeActive ? 1.0 : 0.55}
                  style={{ transition: 'opacity 0.15s ease, stroke-width 0.15s ease' }}
                />
              );
            })}

            {/* Nodes */}
            {laidNodes.map(n => {
              const isSelected = selectedNode?.id === n.id;
              const isHovered = hoveredNode?.id === n.id;
              const isConnected = connectedNodeIds ? connectedNodeIds.has(n.id) : true;
              const isDimmed = connectedNodeIds !== null && !isConnected;
              const isSearched = searchTerm && n.name.toLowerCase().includes(searchTerm.toLowerCase());

              const color = nodeColor(n.node_type);
              const radius = isSelected ? 10 : isHovered ? 9 : 7;

              return (
                <g
                  key={n.id}
                  transform={`translate(${n.x}, ${n.y})`}
                  style={{ cursor: 'pointer', opacity: isDimmed ? 0.2 : 1.0, transition: 'opacity 0.15s ease' }}
                  onMouseEnter={() => setHoveredNode(n)}
                  onMouseLeave={() => setHoveredNode(null)}
                  onClick={e => handleNodeClick(n, e)}
                >
                  {/* Expanded invisible hit target circle (r=22) prevents pointer jitter */}
                  <circle cx={0} cy={0} r={22} fill="transparent" />

                  {/* Halo for Search or Selection */}
                  {(isSelected || isSearched) && (
                    <circle
                      cx={0} cy={0} r={radius + 5}
                      fill="none"
                      stroke={isSelected ? '#171717' : '#0070f3'}
                      strokeWidth={1.5}
                      strokeDasharray={isSelected ? undefined : '3 2'}
                      opacity={0.7}
                    />
                  )}

                  {/* Primary Node Circle */}
                  <circle
                    cx={0} cy={0} r={radius}
                    fill={color}
                    stroke={isSelected ? 'var(--primary)' : isHovered ? '#171717' : 'var(--canvas)'}
                    strokeWidth={isSelected ? 2.5 : isHovered ? 2.0 : 1.5}
                  />

                  {/* Node Label */}
                  <text
                    x={0}
                    y={radius + 14}
                    textAnchor="middle"
                    fontSize={10}
                    fontWeight={isSelected || isHovered ? 600 : 400}
                    fill={isSelected || isHovered ? 'var(--ink)' : 'var(--body)'}
                    fontFamily="var(--font-mono)"
                    style={{ pointerEvents: 'none', userSelect: 'none' }}
                  >
                    {n.name.length > 20 ? n.name.slice(0, 19) + '…' : n.name}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* Node Inspector Side Panel */}
      {selectedNode && (
        <div className="card" style={{ width: 280, flexShrink: 0, overflowY: 'auto', maxHeight: '100%' }}>
          <div className="card-inner" style={{ padding: 'var(--space-md)' }}>
            <div className="flex items-center justify-between mb-2">
              <span className="badge badge-default" style={{ textTransform: 'uppercase', fontSize: 10 }}>
                {selectedNode.node_type}
              </span>
              <button className="btn btn-ghost btn-sm" style={{ padding: 4 }} onClick={() => setSelectedNode(null)}>
                <X size={13} />
              </button>
            </div>

            <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.28px', wordBreak: 'break-all' }}>
              {selectedNode.name}
            </h3>
            <p style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)', marginTop: 4, wordBreak: 'break-all' }}>
              {selectedNode.qualified_name}
            </p>

            <div className="divider" style={{ margin: 'var(--space-sm) 0' }} />

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {selectedNode.file_path && (
                <div>
                  <p className="section-eyebrow" style={{ marginBottom: 2 }}>File</p>
                  <p style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--body)', wordBreak: 'break-all' }}>
                    {selectedNode.file_path}
                  </p>
                </div>
              )}

              {selectedNode.start_line && (
                <div>
                  <p className="section-eyebrow" style={{ marginBottom: 2 }}>Lines</p>
                  <p style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--body)' }}>
                    {selectedNode.start_line} – {selectedNode.end_line ?? selectedNode.start_line}
                  </p>
                </div>
              )}

              <div>
                <p className="section-eyebrow" style={{ marginBottom: 4 }}>
                  Connected Entities ({edges.filter(e => e.source_node_id === selectedNode.id || e.target_node_id === selectedNode.id).length})
                </p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {edges
                    .filter(e => e.source_node_id === selectedNode.id || e.target_node_id === selectedNode.id)
                    .slice(0, 15)
                    .map(e => {
                      const isOut = e.source_node_id === selectedNode.id;
                      const otherId = isOut ? e.target_node_id : e.source_node_id;
                      const other = nodeMap[otherId];
                      if (!other) return null;
                      return (
                        <div
                          key={e.id}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '3px 6px',
                            background: 'var(--canvas-soft)',
                            borderRadius: 'var(--radius-sm)',
                            cursor: 'pointer',
                          }}
                          onClick={() => setSelectedNode(other)}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0 }}>
                            {isOut ? <ArrowRight size={11} color="var(--mute)" /> : <ArrowLeft size={11} color="var(--mute)" />}
                            <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--body)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {other.name}
                            </span>
                          </div>
                          <span className="badge badge-default" style={{ fontSize: 9 }}>
                            {e.edge_type}
                          </span>
                        </div>
                      );
                    })}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

/* ─── Type Legend Filter ─────────────────────────────────────────────────── */
const NODE_TYPES = ['function', 'method', 'class', 'module', 'test', 'apiendpoint'];

const Legend: React.FC<{ selected: Set<string>; onToggle: (t: string) => void }> = ({ selected, onToggle }) => (
  <div className="flex items-center gap-2 wrap">
    {NODE_TYPES.map(t => (
      <button
        key={t}
        onClick={() => onToggle(t)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '3px 9px', borderRadius: 'var(--radius-full)',
          border: `1px solid ${selected.size === 0 || selected.has(t) ? nodeColor(t) : 'var(--hairline)'}`,
          background: selected.size === 0 || selected.has(t) ? `${nodeColor(t)}14` : 'var(--canvas-soft)',
          color: selected.size === 0 || selected.has(t) ? nodeColor(t) : 'var(--mute)',
          fontSize: 11, fontFamily: 'var(--font-mono)', cursor: 'pointer',
          transition: 'all 0.1s ease',
        }}
      >
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: nodeColor(t), display: 'inline-block' }} />
        {t}
      </button>
    ))}
  </div>
);

/* ─── Repository Graph Page ──────────────────────────────────────────────── */
export const RepositoryGraph: React.FC = () => {
  const { repoId } = useParams<{ repoId?: string }>();
  const [repos, setRepos] = useState<RepositoryItem[]>([]);
  const [selectedRepo, setSelectedRepo] = useState(repoId ?? '');
  const [nodes, setNodes] = useState<GraphNodeData[]>([]);
  const [edges, setEdges] = useState<GraphEdgeData[]>([]);
  const [graphStatus, setGraphStatus] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [search, setSearch] = useState('');
  const [filteredTypes, setFilteredTypes] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchTrackedRepos().then(setRepos).catch(console.error);
  }, []);

  useEffect(() => {
    if (!selectedRepo) return;
    let timer: ReturnType<typeof setTimeout>;
    const loadGraph = async () => {
      try {
        const [status, n, e] = await Promise.all([
          fetchGraphStatus(selectedRepo),
          fetchGraphNodes(selectedRepo, 400),
          fetchGraphEdges(selectedRepo),
        ]);
        setGraphStatus(status.status);
        setNodes(n);
        setEdges(e);
        if (status.status === 'BUILDING') {
          timer = setTimeout(loadGraph, 2500);
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    setLoading(true);
    loadGraph();
    return () => clearTimeout(timer);
  }, [selectedRepo]);

  const handleBuild = async () => {
    if (!selectedRepo) return;
    const repo = repos.find(r => r.id === selectedRepo);
    setBuilding(true);
    try {
      await triggerGraphBuild(selectedRepo, repo?.default_branch ?? 'main');
      setGraphStatus('BUILDING');
      const poll = setInterval(async () => {
        try {
          const status = await fetchGraphStatus(selectedRepo);
          setGraphStatus(status.status);
          if (status.status === 'READY') {
            clearInterval(poll);
            setBuilding(false);
            const [n, e] = await Promise.all([
              fetchGraphNodes(selectedRepo, 400),
              fetchGraphEdges(selectedRepo),
            ]);
            setNodes(n);
            setEdges(e);
          }
        } catch {
          // ignore
        }
      }, 2500);
    } catch (e) {
      console.error(e);
      setBuilding(false);
    }
  };

  const toggleType = (t: string) => {
    setFilteredTypes(prev => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  const statusBadge = () => {
    if (graphStatus === 'READY')    return <span className="badge badge-success">Graph Ready</span>;
    if (graphStatus === 'BUILDING') return <span className="badge badge-running">Building</span>;
    if (graphStatus === 'NOT_BUILT')return <span className="badge badge-default">Not Built</span>;
    return null;
  };

  return (
    <>
      {/* Header */}
      <div className="page-header">
        <div className="flex items-start justify-between gap-4 wrap">
          <div>
            <h1 className="page-title">Knowledge Graph</h1>
            <p className="page-subtitle">Interactive deterministic AST symbol graph and call hierarchy.</p>
          </div>
          <div className="flex items-center gap-2">
            {statusBadge()}
            <button className="btn btn-secondary btn-sm" onClick={handleBuild} disabled={building || !selectedRepo}>
              {building ? <RefreshCw size={12} className="spin" /> : <RefreshCw size={12} />}
              Build
            </button>
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="card" style={{ marginBottom: 'var(--space-md)' }}>
        <div className="card-inner" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
          <div className="flex items-center gap-3 wrap">
            {/* Repo selector */}
            <select
              className="input input-sm"
              style={{ maxWidth: 280 }}
              value={selectedRepo}
              onChange={e => setSelectedRepo(e.target.value)}
            >
              <option value="">— Select repository —</option>
              {repos.map(r => (
                <option key={r.id} value={r.id}>{r.full_name}</option>
              ))}
            </select>

            {/* Search */}
            <div style={{ position: 'relative', flex: 1, maxWidth: 320 }}>
              <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--mute)' }} />
              <input
                className="input input-sm"
                style={{ paddingLeft: 32 }}
                placeholder="Search symbols or files..."
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>

            {/* Stats */}
            {nodes.length > 0 && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--mute)' }}>
                {nodes.length} nodes &middot; {edges.length} edges
              </span>
            )}
          </div>

          {/* Node type filter */}
          <Legend selected={filteredTypes} onToggle={toggleType} />
        </div>
      </div>

      {/* Isolated Canvas Area */}
      <div style={{ height: 600 }}>
        {!selectedRepo ? (
          <div className="empty-state" style={{ height: '100%' }}>
            <span className="empty-state-title">Select a repository to view its knowledge graph.</span>
          </div>
        ) : loading ? (
          <div className="empty-state" style={{ height: '100%' }}>
            <RefreshCw size={24} className="spin" style={{ color: 'var(--mute)' }} />
            <span className="empty-state-title">Loading graph...</span>
          </div>
        ) : graphStatus === 'NOT_BUILT' ? (
          <div className="empty-state" style={{ height: '100%' }}>
            <span className="empty-state-title">Knowledge graph not yet built.</span>
            <span className="empty-state-body">Click "Build" to generate the graph for this repository's default branch.</span>
            <button className="btn btn-primary btn-sm" onClick={handleBuild} disabled={building}>
              {building ? <RefreshCw size={12} className="spin" /> : null}
              Build Knowledge Graph
            </button>
          </div>
        ) : nodes.length === 0 ? (
          <div className="empty-state" style={{ height: '100%' }}>
            <span className="empty-state-title">No graph data available.</span>
          </div>
        ) : (
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            searchTerm={search}
            selectedTypes={filteredTypes}
          />
        )}
      </div>
    </>
  );
};
