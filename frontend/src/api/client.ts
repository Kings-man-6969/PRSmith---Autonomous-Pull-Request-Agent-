import axios from 'axios';

export const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export const apiClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
});

/* ─── Jobs ─────────────────────────────────────────────────────────────────── */
export interface JobListItem {
  id: string;
  repository: string;
  pr_number: number;
  pr_title: string;
  status: string;
  risk_level: string;
  confidence_score: number;
  created_at: string;
  completed_at: string | null;
}

export interface ReviewFindingData {
  id: string;
  finding_id: string;
  severity: string;
  category: string;
  file_path: string;
  symbol_name: string | null;
  description: string;
  confidence: number;
  repairability: string;
  status: string;
  evidence: Array<{
    entity_type: string;
    entity_name: string;
    relationship: string;
    verified_in_graph: boolean;
  }>;
  affected_entities: string[];
}

export interface JobDetailData {
  id: string;
  repository: string;
  pr_number: number;
  pr_title: string;
  base_sha: string;
  head_sha: string;
  status: string;
  risk_level: string;
  risk_score: number;
  confidence_score: number;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  review_runs: Array<{
    id: string;
    status: string;
    findings: ReviewFindingData[];
  }>;
  repair_runs: Array<{
    id: string;
    status: string;
    iterations: Array<{
      iteration_number: number;
      status: string;
      validation_status: string;
      error_summary: string | null;
    }>;
  }>;
  validation_runs: Array<{
    id: string;
    stage: string;
    passed: boolean;
    duration_ms: number;
    results: Array<{
      layer: string;
      command: string;
      passed: boolean;
      exit_code: number;
      stdout: string;
      stderr: string;
      failure_class: string | null;
    }>;
  }>;
}

export interface PatchData {
  id: string;
  diff_hash: string;
  diff_content: string;
  files_changed: string[];
  lines_added: number;
  lines_deleted: number;
  is_valid_syntax: boolean;
  is_within_scope: boolean;
  created_at: string;
}

/* ─── Repositories ──────────────────────────────────────────────────────────── */
export interface RepositoryItem {
  id: string;
  full_name: string;
  owner: string;
  name: string;
  default_branch: string;
  created_at: string | null;
  monitoring_enabled?: boolean;
  auto_repair_enabled?: boolean;
  watched_branches?: string[];
}

export interface GitHubRepoItem {
  id: number;
  full_name: string;
  owner: string;
  name: string;
  private: boolean;
  default_branch: string;
  updated_at: string;
  open_issues_count: number;
}

export interface GraphStatusData {
  status: 'NOT_BUILT' | 'READY' | 'BUILDING';
  graph_version_id?: string;
  commit_sha?: string;
  is_incremental?: boolean;
  node_count: number;
  edge_count: number;
  last_verified?: string | null;
}

export interface GraphNodeData {
  id: string;
  node_type: string;
  name: string;
  qualified_name: string;
  file_path: string;
  start_line: number | null;
  end_line: number | null;
  source_commit: string | null;
}

export interface GraphEdgeData {
  id: string;
  source_node_id: string;
  target_node_id: string;
  edge_type: string;
}

export interface OpenPRItem {
  number: number;
  title: string;
  head_branch: string;
  created_at: string;
  user: string;
}

/* ─── API Functions ─────────────────────────────────────────────────────────── */

// Jobs
export const fetchJobs = async (): Promise<JobListItem[]> => {
  const res = await apiClient.get<JobListItem[]>('/jobs');
  return res.data;
};

export const fetchJobDetail = async (jobId: string): Promise<JobDetailData> => {
  const res = await apiClient.get<JobDetailData>(`/jobs/${jobId}`);
  return res.data;
};

export const fetchJobPatches = async (jobId: string): Promise<PatchData[]> => {
  const res = await apiClient.get<PatchData[]>(`/jobs/${jobId}/patches`);
  return res.data;
};

// Repositories (tracked)
export const fetchTrackedRepos = async (): Promise<RepositoryItem[]> => {
  const res = await apiClient.get<RepositoryItem[]>('/repositories');
  return res.data;
};

export const fetchGitHubRepos = async (): Promise<GitHubRepoItem[]> => {
  const res = await apiClient.get<GitHubRepoItem[]>('/repositories/discover');
  return res.data;
};

export const registerRepo = async (fullName: string): Promise<RepositoryItem> => {
  const res = await apiClient.post<RepositoryItem>('/repositories/register', {
    full_name: fullName,
  });
  return res.data;
};

export const unregisterRepo = async (repoId: string): Promise<void> => {
  await apiClient.delete(`/repositories/${repoId}`);
};

export const setMonitoring = async (
  repoId: string,
  enabled: boolean
): Promise<void> => {
  await apiClient.patch(`/repositories/${repoId}/monitoring`, { enabled });
};

export const setAutoRepair = async (
  repoId: string,
  enabled: boolean
): Promise<void> => {
  await apiClient.patch(`/repositories/${repoId}/auto-repair`, { enabled });
};

export const setWatchedBranches = async (
  repoId: string,
  branches: string[]
): Promise<void> => {
  await apiClient.patch(`/repositories/${repoId}/branches`, { branches });
};

export const triggerFindingRepair = async (
  jobId: string,
  findingId: string
): Promise<{ status: string; task_id: string; job_id: string; finding_id: string }> => {
  const res = await apiClient.post<{ status: string; task_id: string; job_id: string; finding_id: string }>(
    `/jobs/${jobId}/findings/${findingId}/repair`
  );
  return res.data;
};

export const triggerGraphBuild = async (
  repoId: string,
  branch: string
): Promise<{ task_id: string }> => {
  const res = await apiClient.post<{ task_id: string }>(
    `/repositories/${repoId}/graph/build`,
    { branch }
  );
  return res.data;
};

export const triggerReview = async (
  repoId: string,
  prNumber: number
): Promise<{ job_id: string }> => {
  const res = await apiClient.post<{ job_id: string }>(
    `/repositories/${repoId}/dispatch/review`,
    { pr_number: prNumber }
  );
  return res.data;
};

export const fetchGraphStatus = async (repoId: string): Promise<GraphStatusData> => {
  const res = await apiClient.get<GraphStatusData>(
    `/repositories/${repoId}/graph/status`
  );
  return res.data;
};

export const fetchGraphNodes = async (
  repoId: string,
  limit = 500
): Promise<GraphNodeData[]> => {
  const res = await apiClient.get<GraphNodeData[]>(
    `/repositories/${repoId}/graph/nodes?limit=${limit}`
  );
  return res.data;
};

export const fetchGraphEdges = async (repoId: string): Promise<GraphEdgeData[]> => {
  const res = await apiClient.get<GraphEdgeData[]>(
    `/repositories/${repoId}/graph/edges`
  );
  return res.data;
};

export const fetchOpenPRs = async (
  repoId: string
): Promise<OpenPRItem[]> => {
  const res = await apiClient.get<OpenPRItem[]>(
    `/repositories/${repoId}/prs`
  );
  return res.data;
};

// Legacy alias kept for compatibility
export const fetchRepositoryGraphStatus = fetchGraphStatus;

/* ─── Authentication ───────────────────────────────────────────────────────── */
export interface UserProfile {
  id: string;
  github_id: number;
  username: string;
  email?: string | null;
  avatar_url?: string | null;
  role: string;
  created_at?: string | null;
}

export const fetchCurrentUser = async (): Promise<UserProfile> => {
  const res = await apiClient.get<UserProfile>('/auth/me');
  return res.data;
};

export const logoutUser = async (): Promise<void> => {
  await apiClient.post('/auth/logout');
};
