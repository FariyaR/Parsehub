"use client";
import apiClient from "@/lib/apiClient";
import { getApiHeaders } from "@/lib/apiBase";
import { getResponseMessage, readResponseData } from "@/lib/response";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Play,
  BookOpen,
  LineChart,
  Clock,
  CheckCircle2,
  AlertCircle,
  Loader2,
  RefreshCw,
  BarChart3,
  TrendingUp,
  FileJson,
  Calendar,
  User,
  Globe,
  Activity,
  ExternalLink,
  X,
} from "lucide-react";
import Header from "@/components/Header";
import SchedulerModal from "@/components/SchedulerModal";
import ColumnStatisticsModal from "@/components/ColumnStatisticsModal";
import CSVDataModal from "@/components/CSVDataModal";

interface ProjectData {
  id: number;
  token: string;
  title: string;
  name?: string;
  owner_email: string;
  projecturl?: string;
  main_site?: string;
  created_at?: string;
  updated_at?: string;
  last_run?: {
    status: string;
    pages: number;
    pages_scraped?: number;
    start_time: string;
    run_token: string;
    end_time?: string;
    duration_seconds?: number;
    created_at?: string;
    updated_at?: string;
  } | null;
  run_stats?: {
    total_runs: number;
    completed_runs: number;
    active_runs: number;
    cancelled_runs: number;
    pages_scraped: number;
    last_run_date?: string;
    average_pages_per_run: number;
    success_rate: number;
  } | null;
}

interface Metadata {
  id: number;
  project_name: string;
  region?: string;
  country?: string;
  brand?: string;
  website_url?: string;
  total_pages?: number;
  total_products?: number;
  current_page_scraped?: number;
  current_product_scraped?: number;
  last_run_date?: string;
  status?: string;
}

interface RunStats {
  total_runs?: number;
  completed_runs?: number;
  pages_scraped?: number;
  products_scraped?: number;
  last_run_date?: string;
  average_pages_per_run?: number;
  success_rate?: number;
}

interface ProductStats {
  statistics?: {
    total_products: number;
    total_runs_with_data: number;
    latest_extraction: string;
    top_countries: Array<{ country: string; count: number }>;
    top_brands: Array<{ brand: string; count: number }>;
  };
}

interface Analytics {
  overview: {
    total_runs: number;
    completed_runs: number;
    total_records_scraped: number;
    unique_records_estimate: number;
    total_pages_analyzed: number;
    progress_percentage: number;
  };
  performance: {
    items_per_minute: number;
    estimated_completion_time: string | null;
    estimated_total_items: number;
    average_run_duration_seconds: number;
    current_items_count: number;
  };
  recovery: {
    in_recovery: boolean;
    status: string;
    total_recovery_attempts: number;
  };
  runs_history: Array<any>;
  data_quality: {
    average_completion_percentage: number;
    total_fields: number;
  };
  timeline: Array<any>;
}

export default function ProjectDetailsPage() {
  const params = useParams();
  const router = useRouter();
  const token = params.token as string;

  const [project, setProject] = useState<ProjectData | null>(null);
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [productStats, setProductStats] = useState<ProductStats | null>(null);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [isProjectRunning, setIsProjectRunning] = useState(false);
  const [isWaitingForRun, setIsWaitingForRun] = useState(false);
  const [runProgress, setRunProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [showScheduler, setShowScheduler] = useState(false);
  const [showStatsModal, setShowStatsModal] = useState(false);
  const [showCSVModal, setShowCSVModal] = useState(false);

  useEffect(() => {
    fetchProjectDetails();
    // Refresh every 5 seconds when running, every 10 seconds otherwise
    const interval = setInterval(
      fetchProjectDetails,
      isProjectRunning ? 5000 : 10000,
    );
    return () => clearInterval(interval);
  }, [token, isProjectRunning]);

  // Background refresh every 1 minute regardless of project state
  useEffect(() => {
    const backgroundInterval = setInterval(() => {
      fetchProjectDetails();
    }, 60000); // 60 seconds = 1 minute
    return () => clearInterval(backgroundInterval);
  }, [token]);

  // Auto-stop when pages_scraped reaches total_pages
  useEffect(() => {
    if (
      isProjectRunning &&
      metadata?.current_page_scraped &&
      metadata?.total_pages &&
      metadata.current_page_scraped >= metadata.total_pages
    ) {
      // Auto-cancel the run when target pages reached
      console.log(
        `[Auto-Stop] Pages reached target: ${metadata.current_page_scraped}/${metadata.total_pages}`,
      );
      handleCancelRun();
      setSuccessMessage(
        `✅ Auto-stopped! Scraped ${metadata.current_page_scraped} of ${metadata.total_pages} target pages.`,
      );
    }
  }, [isProjectRunning, metadata?.current_page_scraped, metadata?.total_pages]);

  const fetchProjectDetails = async () => {
    try {
      setError(null);
      let projectData: ProjectData | null = null;
      let metadataData: Metadata | null = null;

      // Fetch project details
      const projectResponse = await fetch(`/api/projects/${token}`, {
        headers: getApiHeaders(),
      });

      if (projectResponse.ok) {
        const raw = await projectResponse.json();
        projectData = raw?.data ?? raw;
        setProject(projectData);
      }

      // Fetch metadata for this project FIRST before calculating progress
      const metadataResponse = await fetch(
        `/api/metadata?project_token=${token}`,
        { headers: getApiHeaders() },
      );

      if (metadataResponse.ok) {
        const metadataResponseData = await metadataResponse.json();
        // Get the first metadata record from the records array
        if (
          metadataResponseData.records &&
          metadataResponseData.records.length > 0
        ) {
          metadataData = metadataResponseData.records[0];
          setMetadata(metadataData);
        }
      }

      // NOW check if project is running and calculate progress with proper metadata
      if (projectData?.last_run?.status === "running") {
        setIsProjectRunning(true);
        setIsWaitingForRun(false);
        // Calculate progress based on actual pages_scraped vs total pages
        const pagesScrapped = projectData?.last_run?.pages_scraped || 0;
        const totalPages = metadataData?.total_pages || 1;
        const progress = Math.min((pagesScrapped / totalPages) * 100, 99);
        setRunProgress(progress);
      } else {
        setIsProjectRunning(false);
        setRunProgress(0);
        // Only clear waiting state if it's been more than 2 seconds since we set it
        if (isWaitingForRun) {
          // Keep waiting state active
        }
      }

      // Fetch product statistics if project has ID
      if (projectData?.id) {
        try {
          const productsStatsResponse = await fetch(
            `/api/products/${projectData.id}/stats`,
            { headers: getApiHeaders() },
          );

          if (productsStatsResponse.ok) {
            const statsData = await productsStatsResponse.json();
            setProductStats(statsData);
          }
        } catch (err) {
          console.log("Product stats not available for this project");
        }
      }

      // Fetch analytics
      try {
        const analyticsResponse = await fetch(
          `/api/projects/${token}/analytics`,
          { headers: getApiHeaders() },
        );

        if (analyticsResponse.ok) {
          const analyticsData = await analyticsResponse.json();
          setAnalytics(analyticsData);
        }
      } catch (err) {
        console.log("Analytics not available for this project");
      }

      setLastUpdate(new Date());
    } catch (err) {
      console.error("Error fetching project details:", err);
      setError(err instanceof Error ? err.message : "Failed to load project");
    } finally {
      setLoading(false);
    }
  };

  const handleRunProject = async () => {
    try {
      setRunning(true);
      setError(null);
      setSuccessMessage(null);
      setIsWaitingForRun(true);

      // Get pages from metadata or use default
      const pages = metadata?.total_pages || 1;

      const response = await fetch("/api/projects/run", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getApiHeaders(),
        },
        body: JSON.stringify({
          project_token: token,
          pages: pages,
        }),
      });

      const data = await readResponseData<Record<string, any>>(response);

      // Check if auto-stop prevented the run
      if (!response.ok) {
        if (data.metadata?.pages_scraped >= data.metadata?.total_pages) {
          setError(
            `✅ Scraping Complete! Already collected ${data.metadata.pages_scraped} of ${data.metadata.total_pages} target pages. ` +
              `Use "View CSV" to download your data.`,
          );
        } else {
          setError(
            getResponseMessage(
              data,
              `Failed to start run: ${response.statusText}`,
            ),
          );
        }
        setIsProjectRunning(false);
        setIsWaitingForRun(false);
        setRunning(false);
        return;
      }

      console.log("Run started:", data);

      setSuccessMessage(`✅ Project queued! Waiting for execution...`);

      // Refresh project data immediately
      await fetchProjectDetails();

      // Poll every 1 second to detect when project actually starts running
      let pollCount = 0;
      const pollInterval = setInterval(async () => {
        pollCount++;
        await fetchProjectDetails();

        // Stop polling after 60 seconds
        if (pollCount >= 60) {
          clearInterval(pollInterval);
          setIsWaitingForRun(false);
          setRunning(false);
        }
      }, 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start run");
      setIsProjectRunning(false);
      setIsWaitingForRun(false);
      setRunning(false);
    }
  };

  const handleScheduleClick = () => {
    setShowScheduler(true);
  };

  const handleViewStats = () => {
    setShowStatsModal(true);
  };

  const handleViewCSV = () => {
    setShowCSVModal(true);
  };

  const handleCancelRun = async () => {
    setActionLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/runs/${token}/cancel`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getApiHeaders(),
        },
      });

      const data = await readResponseData<Record<string, unknown>>(response);

      if (!response.ok) {
        throw new Error(
          getResponseMessage(data, `Failed to cancel run: ${response.statusText}`),
        );
      }

      setSuccessMessage("Run cancelled successfully.");
      await fetchProjectDetails();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel run");
    } finally {
      setActionLoading(false);
    }
  };

  const getStatusColor = (status?: string) => {
    switch (status?.toLowerCase()) {
      case "running":
        return "bg-blue-500/20 text-blue-400 border-blue-500/30";
      case "complete":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
      case "completed":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
      case "failed":
        return "bg-red-500/20 text-red-400 border-red-500/30";
      case "error":
        return "bg-red-500/20 text-red-400 border-red-500/30";
      case "queued":
      case "pending":
        return "bg-amber-500/20 text-amber-400 border-amber-500/30";
      default:
        return "bg-slate-500/20 text-slate-400 border-slate-500/30";
    }
  };

  const getStatusIcon = (status?: string) => {
    switch (status?.toLowerCase()) {
      case "running":
        return <Loader2 className="w-4 h-4 animate-spin" />;
      case "complete":
      case "completed":
        return <CheckCircle2 className="w-4 h-4" />;
      case "failed":
      case "error":
        return <AlertCircle className="w-4 h-4" />;
      case "queued":
      case "pending":
        return <Clock className="w-4 h-4" />;
      default:
        return <FileJson className="w-4 h-4" />;
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950">
        <Header />
        <div className="flex items-center justify-center h-96">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="w-8 h-8 text-blue-400 animate-spin" />
            <p className="text-slate-400">Loading project details...</p>
          </div>
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="min-h-screen bg-slate-950">
        <Header />
        <div className="max-w-7xl mx-auto px-6 py-8">
          <button
            onClick={() => router.back()}
            className="inline-flex items-center gap-2 text-slate-400 hover:text-slate-200 mb-6"
          >
            <ArrowLeft className="w-4 h-4" />
            Back
          </button>
          <div className="text-center py-16">
            <AlertCircle className="w-12 h-12 text-red-400 mx-auto mb-4" />
            <p className="text-slate-400 text-lg">Project not found</p>
          </div>
        </div>
      </div>
    );
  }

  const projectName = project.name || project.title || project.token || "Unknown";
  const lastRunStatus = project.last_run?.status || "not_run";

  return (
    <div className="min-h-screen bg-slate-950">
      <Header />

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Header Section */}
        <div className="flex items-start justify-between mb-8">
          <div>
            <button
              onClick={() => router.back()}
              className="inline-flex items-center gap-2 text-slate-400 hover:text-slate-200 mb-4 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>
            <div>
              <h1 className="text-3xl font-bold text-white mb-2">
                {projectName}
              </h1>
              <div className="flex items-center gap-4 flex-wrap">
                <span
                  className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium border ${getStatusColor(lastRunStatus)}`}
                >
                  {getStatusIcon(lastRunStatus)}
                  {lastRunStatus === "complete"
                    ? "Completed"
                    : lastRunStatus === "complete"
                      ? "Completed"
                      : lastRunStatus.charAt(0).toUpperCase() +
                        lastRunStatus.slice(1)}
                </span>
                <span className="text-slate-400 text-sm">
                  Token:{" "}
                  <code className="bg-slate-800/50 px-2 py-1 rounded">
                    {token}
                  </code>
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => router.push(`/projects/${token}`)}
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-indigo-500/25"
              title="View details"
            >
              <ExternalLink size={14} />
            </button>
            <button
              onClick={handleRunProject}
              disabled={
                running ||
                isProjectRunning ||
                actionLoading ||
                !!(
                  metadata?.current_page_scraped &&
                  metadata?.total_pages &&
                  metadata.current_page_scraped >= metadata.total_pages
                )
              }
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-700 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-blue-500/25 disabled:shadow-none"
              title={
                metadata?.current_page_scraped &&
                metadata?.total_pages &&
                metadata.current_page_scraped >= metadata.total_pages
                  ? `Completed: ${metadata.current_page_scraped}/${metadata.total_pages} pages scraped`
                  : "Run project"
              }
            >
              {running || actionLoading ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )}
            </button>
            <button
              onClick={handleScheduleClick}
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-purple-500/25"
              title="Schedule"
            >
              <Clock size={14} />
            </button>
            <button
              onClick={handleViewStats}
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-orange-600 hover:bg-orange-700 text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-orange-500/25"
              title="Statistics"
            >
              <BarChart3 size={14} />
            </button>
            <button
              onClick={handleViewCSV}
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-emerald-500/25"
              title="View CSV"
            >
              <FileJson size={14} />
            </button>
            {isProjectRunning && (
              <button
                onClick={handleCancelRun}
                disabled={actionLoading}
                className="inline-flex items-center gap-1.5 px-3 py-2 bg-red-600 hover:bg-red-700 disabled:bg-slate-700 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-red-500/25 disabled:shadow-none"
                title="Cancel run"
              >
                {actionLoading ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <X size={14} />
                )}
              </button>
            )}

            <div className="w-px h-6 bg-slate-600 mx-1" />

            <button
              onClick={fetchProjectDetails}
              disabled={isProjectRunning}
              className="inline-flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 disabled:bg-slate-800/50 disabled:opacity-50 text-slate-200 rounded-lg transition-colors"
              title="Refresh data"
            >
              <RefreshCw
                className={`w-4 h-4 ${isProjectRunning ? "animate-spin" : ""}`}
              />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-lg flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0" />
            <p className="text-red-400">{error}</p>
          </div>
        )}

        {successMessage && (
          <div className="mb-6 p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-lg flex items-center gap-3 animate-in">
            <CheckCircle2 className="w-5 h-5 text-emerald-400 flex-shrink-0" />
            <p className="text-emerald-400">{successMessage}</p>
          </div>
        )}

        {/* Waiting for Run Banner */}
        {isWaitingForRun && !isProjectRunning && (
          <div className="mb-8 p-6 bg-gradient-to-r from-amber-500/10 to-orange-500/10 border-2 border-amber-500/50 rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <Loader2 className="w-5 h-5 text-amber-400 animate-spin" />
                <div>
                  <h3 className="text-lg font-bold text-white">
                    ⏳ Initializing Project Run
                  </h3>
                  <p className="text-sm text-amber-300">
                    Waiting for execution to start...
                  </p>
                </div>
              </div>
            </div>

            {/* Loading Animation */}
            <div className="w-full bg-slate-700 rounded-full h-3 overflow-hidden border border-amber-500/30">
              <div className="bg-gradient-to-r from-amber-500 via-orange-500 to-amber-500 h-full rounded-full animate-pulse" />
            </div>

            {/* Loading Message */}
            <div className="mt-4 text-center">
              <p className="text-sm text-amber-300 font-medium">
                Project queued. Starting soon...
              </p>
            </div>
          </div>
        )}

        {/* Live Monitoring Banner */}
        {isProjectRunning && (
          <div className="mb-8 p-6 bg-gradient-to-r from-blue-500/10 to-cyan-500/10 border-2 border-blue-500/50 rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <Activity className="w-5 h-5 text-blue-400 animate-pulse" />
                <div>
                  <h3 className="text-lg font-bold text-white">
                    🔴 LIVE: Project Running
                  </h3>
                  <p className="text-sm text-blue-300">
                    Real-time monitoring active
                  </p>
                </div>
              </div>
              <div className="text-right">
                <p className="text-2xl font-bold text-emerald-400">
                  {Math.round(runProgress)}%
                </p>
                <p className="text-xs text-slate-400">Completion</p>
              </div>
            </div>

            {/* Progress Bar */}
            <div className="w-full bg-slate-700 rounded-full h-4 overflow-hidden border border-blue-500/30">
              <div
                className="bg-gradient-to-r from-blue-500 to-cyan-500 h-full rounded-full transition-all duration-500 flex items-center justify-center relative"
                style={{ width: `${Math.max(runProgress, 5)}%` }}
              >
                <div className="absolute inset-0 bg-white/20 animate-pulse rounded-full" />
              </div>
            </div>

            {/* Live Stats */}
            {project?.last_run && (
              <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-slate-800/40 rounded-lg p-3 border border-slate-700/50">
                  <p className="text-xs text-slate-500 mb-1">Status</p>
                  <p className="text-sm font-bold text-blue-400 capitalize">
                    {project.last_run.status}
                  </p>
                </div>
                <div className="bg-slate-800/40 rounded-lg p-3 border border-slate-700/50">
                  <p className="text-xs text-slate-500 mb-1">Pages Scraped</p>
                  <p className="text-sm font-bold text-emerald-400">
                    {project.last_run.pages_scraped ||
                      project.last_run.pages ||
                      0}{" "}
                    / {metadata?.total_pages || "?"}
                  </p>
                </div>
                <div className="bg-slate-800/40 rounded-lg p-3 border border-slate-700/50">
                  <p className="text-xs text-slate-500 mb-1">Started</p>
                  <p className="text-sm font-bold text-cyan-400">
                    {project.last_run.start_time
                      ? new Date(
                          project.last_run.start_time,
                        ).toLocaleTimeString()
                      : "Just now"}
                  </p>
                </div>
                <div className="bg-slate-800/40 rounded-lg p-3 border border-slate-700/50">
                  <p className="text-xs text-slate-500 mb-1">Duration</p>
                  <p className="text-sm font-bold text-amber-400">
                    {project.last_run.duration_seconds
                      ? `${Math.floor(project.last_run.duration_seconds / 60)}m ${
                          project.last_run.duration_seconds % 60
                        }s`
                      : "In progress"}
                  </p>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
          {/* Main Details */}
          <div className="lg:col-span-2 space-y-6">
            {/* Project Info Card */}
            <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
              <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-blue-400" />
                Project Information
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {project.owner_email && (
                  <div>
                    <label className="flex items-center gap-2 text-xs font-semibold text-slate-400 uppercase mb-1">
                      <User className="w-4 h-4" />
                      Owner
                    </label>
                    <p className="text-slate-200">{project.owner_email}</p>
                  </div>
                )}

                {metadata?.website_url && (
                  <div>
                    <label className="flex items-center gap-2 text-xs font-semibold text-slate-400 uppercase mb-1">
                      <Globe className="w-4 h-4" />
                      Website
                    </label>
                    <a
                      href={metadata.website_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-400 hover:text-blue-300 break-all"
                    >
                      {metadata.website_url}
                    </a>
                  </div>
                )}

                {metadata?.region && (
                  <div>
                    <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                      Region
                    </label>
                    <p className="text-slate-200">{metadata.region}</p>
                  </div>
                )}

                {metadata?.country && (
                  <div>
                    <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                      Country
                    </label>
                    <p className="text-slate-200">{metadata.country}</p>
                  </div>
                )}

                {metadata?.brand && (
                  <div>
                    <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                      Brand
                    </label>
                    <p className="text-slate-200">{metadata.brand}</p>
                  </div>
                )}

                {project.created_at && (
                  <div>
                    <label className="flex items-center gap-2 text-xs font-semibold text-slate-400 uppercase mb-1">
                      <Calendar className="w-4 h-4" />
                      Created
                    </label>
                    <p className="text-slate-200">
                      {new Date(project.created_at).toLocaleDateString()}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Metadata Details */}
            {metadata && (
              <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
                <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                  <FileJson className="w-5 h-5 text-cyan-400" />
                  Project Metadata
                </h2>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {metadata.project_name && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Project Name
                      </label>
                      <p className="text-slate-200">{metadata.project_name}</p>
                    </div>
                  )}

                  {metadata.total_pages && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Total Pages
                      </label>
                      <p className="text-2xl font-bold text-cyan-400">
                        {(metadata.total_pages ?? 0).toLocaleString()}
                      </p>
                    </div>
                  )}

                  {metadata.current_page_scraped !== undefined &&
                    metadata.total_pages && (
                      <div>
                        <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                          Pages Scraped
                        </label>
                        <p className="text-2xl font-bold text-emerald-400 mb-2">
                          {metadata.current_page_scraped} /{" "}
                          {metadata.total_pages}
                        </p>
                        {/* Progress Bar */}
                        <div className="w-full bg-slate-700 rounded-full h-2 overflow-hidden border border-slate-600/50">
                          <div
                            className={`h-full rounded-full transition-all duration-500 ${
                              metadata.current_page_scraped >=
                              metadata.total_pages
                                ? "bg-gradient-to-r from-emerald-500 to-green-500"
                                : "bg-gradient-to-r from-blue-500 to-cyan-500"
                            }`}
                            style={{
                              width: `${Math.min(
                                (metadata.current_page_scraped /
                                  metadata.total_pages) *
                                  100,
                                100,
                              )}%`,
                            }}
                          />
                        </div>
                        {metadata.current_page_scraped >=
                          metadata.total_pages && (
                          <p className="text-xs text-emerald-400 mt-1 font-semibold">
                            ✅ Scraping Complete
                          </p>
                        )}
                      </div>
                    )}

                  {metadata.total_products && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Total Products
                      </label>
                      <p className="text-2xl font-bold text-emerald-400">
                        {(metadata.total_products ?? 0).toLocaleString()}
                      </p>
                    </div>
                  )}

                  {metadata.website_url && (
                    <div>
                      <label className="flex items-center gap-2 text-xs font-semibold text-slate-400 uppercase mb-1">
                        <Globe className="w-4 h-4" />
                        Website URL
                      </label>
                      <a
                        href={metadata.website_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-400 hover:text-blue-300 break-all"
                      >
                        {metadata.website_url}
                      </a>
                    </div>
                  )}

                  {metadata.region && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Region
                      </label>
                      <p className="text-slate-200">{metadata.region}</p>
                    </div>
                  )}

                  {metadata.country && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Country
                      </label>
                      <p className="text-slate-200">{metadata.country}</p>
                    </div>
                  )}

                  {metadata.brand && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Brand
                      </label>
                      <p className="text-slate-200">{metadata.brand}</p>
                    </div>
                  )}

                  {metadata.status && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Status
                      </label>
                      <span
                        className={`inline-flex px-3 py-1.5 rounded-lg text-sm font-medium border ${
                          metadata.status === "active"
                            ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                            : metadata.status === "completed"
                              ? "bg-blue-500/10 text-blue-400 border-blue-500/30"
                              : "bg-slate-500/10 text-slate-400 border-slate-500/30"
                        }`}
                      >
                        {metadata.status.charAt(0).toUpperCase() +
                          metadata.status.slice(1)}
                      </span>
                    </div>
                  )}

                  {metadata.last_run_date && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Last Run Date
                      </label>
                      <p className="text-slate-200">
                        {metadata.last_run_date ? new Date(metadata.last_run_date).toLocaleString() : "—"}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Last Run Details */}
            {project.last_run && (
              <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
                <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                  <LineChart className="w-5 h-5 text-purple-400" />
                  Last Run Details
                </h2>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div>
                    <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                      Status
                    </label>
                    <span
                      className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium border ${getStatusColor(project.last_run.status)}`}
                    >
                      {getStatusIcon(project.last_run.status)}
                      {project.last_run.status.charAt(0).toUpperCase() +
                        project.last_run.status.slice(1)}
                    </span>
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                      Pages Scraped
                    </label>
                    <p className="text-2xl font-bold text-blue-400">
                      {project.last_run.pages_scraped ||
                        project.last_run.pages ||
                        0}
                    </p>
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                      Started
                    </label>
                    <p className="text-slate-200">
                      {project.last_run.start_time
                        ? (project.last_run.start_time ? new Date(project.last_run.start_time).toLocaleString() : "—")
                        : "—"}
                    </p>
                  </div>

                  {project.last_run.end_time && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Ended
                      </label>
                      <p className="text-slate-200">
                        {project.last_run.end_time ? new Date(project.last_run.end_time).toLocaleString() : "—"}
                      </p>
                    </div>
                  )}

                  {project.last_run.duration_seconds && (
                    <div>
                      <label className="text-xs font-semibold text-slate-400 uppercase mb-1 block">
                        Duration
                      </label>
                      <p className="text-slate-200">
                        {Math.floor(project.last_run.duration_seconds / 60)}m{" "}
                        {project.last_run.duration_seconds % 60}s
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Product Data Statistics */}
            {productStats?.statistics && (
              <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
                <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-emerald-400" />
                  Ingested Product Data
                </h2>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {/* Main Stats */}
                  <div className="space-y-4">
                    <div className="bg-emerald-500/10 rounded-lg p-4 border border-emerald-500/30">
                      <p className="text-xs font-semibold text-emerald-400 uppercase mb-1">
                        Total Products
                      </p>
                      <p className="text-3xl font-bold text-white">
                        {(productStats.statistics.total_products ?? 0).toLocaleString()}
                      </p>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="bg-blue-500/10 rounded-lg p-3 border border-blue-500/30">
                        <p className="text-xs font-semibold text-blue-400 uppercase mb-1">
                          Runs
                        </p>
                        <p className="text-2xl font-bold text-white">
                          {productStats.statistics.total_runs_with_data}
                        </p>
                      </div>
                      <div className="bg-purple-500/10 rounded-lg p-3 border border-purple-500/30">
                        <p className="text-xs font-semibold text-purple-400 uppercase mb-1">
                          Latest
                        </p>
                        <p className="text-sm font-semibold text-white">
                          {productStats.statistics.latest_extraction}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Countries and Brands */}
                  <div className="space-y-4">
                    {/* Top Countries */}
                    {productStats.statistics.top_countries &&
                      productStats.statistics.top_countries.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold text-slate-400 uppercase mb-2">
                            Top Countries
                          </p>
                          <div className="space-y-2">
                            {productStats.statistics.top_countries
                              .slice(0, 5)
                              .map((country, idx) => (
                                <div
                                  key={idx}
                                  className="flex items-center justify-between"
                                >
                                  <span className="text-sm text-slate-300">
                                    {country.country}
                                  </span>
                                  <div className="flex items-center gap-2">
                                    <div className="w-24 bg-slate-700 rounded-full h-2 overflow-hidden">
                                      <div
                                        className="bg-emerald-500 h-full rounded-full"
                                        style={{
                                          width: `${(country.count / productStats.statistics.total_products) * 100}%`,
                                        }}
                                      />
                                    </div>
                                    <span className="text-xs text-slate-400 w-12 text-right">
                                      {country.count}
                                    </span>
                                  </div>
                                </div>
                              ))}
                          </div>
                        </div>
                      )}
                  </div>
                </div>
              </div>
            )}

            {/* Analytics: Run History & Timeline */}
            {analytics &&
              analytics.runs_history &&
              analytics.runs_history.length > 0 && (
                <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
                  <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                    <Clock className="w-5 h-5 text-cyan-400" />
                    Recent Run History
                  </h2>

                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-slate-700/50">
                          <th className="text-left text-xs font-semibold text-slate-400 uppercase py-3 px-4">
                            Status
                          </th>
                          <th className="text-left text-xs font-semibold text-slate-400 uppercase py-3 px-4">
                            Records
                          </th>
                          <th className="text-left text-xs font-semibold text-slate-400 uppercase py-3 px-4">
                            Pages
                          </th>
                          <th className="text-left text-xs font-semibold text-slate-400 uppercase py-3 px-4">
                            Duration
                          </th>
                          <th className="text-left text-xs font-semibold text-slate-400 uppercase py-3 px-4">
                            Started
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {analytics.runs_history
                          .slice(0, 5)
                          .map((run: any, idx: number) => (
                            <tr
                              key={idx}
                              className="border-b border-slate-700/30 hover:bg-slate-700/20 transition-colors"
                            >
                              <td className="py-3 px-4">
                                <span
                                  className={`inline-flex items-center px-2 py-1 rounded text-xs font-semibold border ${getStatusColor(run.status)}`}
                                >
                                  {getStatusIcon(run.status)}
                                  <span className="ml-1 capitalize">
                                    {run.status}
                                  </span>
                                </span>
                              </td>
                              <td className="py-3 px-4 font-semibold text-white">
                                {(run.records_count || 0).toLocaleString()}
                              </td>
                              <td className="py-3 px-4 text-slate-300">
                                {run.pages_scraped || 0}
                              </td>
                              <td className="py-3 px-4 text-slate-300">
                                {run.duration_seconds
                                  ? `${Math.round(run.duration_seconds / 60)}m`
                                  : "—"}
                              </td>
                              <td className="py-3 px-4 text-sm text-slate-400">
                                {run.start_time
                                  ? new Date(run.start_time).toLocaleString()
                                  : run.created_at
                                    ? new Date(run.created_at).toLocaleString()
                                    : "—"}
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>

                  {analytics.runs_history.length > 5 && (
                    <div className="mt-4 text-center">
                      <p className="text-xs text-slate-400">
                        Showing 5 of {analytics.runs_history.length} total runs
                      </p>
                    </div>
                  )}
                </div>
              )}

            {/* Data Quality & Recovery Status */}
            {analytics &&
              ((analytics.data_quality?.total_fields ?? 0) > 0 ||
                (analytics.recovery?.total_recovery_attempts ?? 0) > 0) && (
                <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
                  <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                    <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                    Data Quality & Recovery
                  </h2>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Data Quality */}
                    {(analytics.data_quality?.total_fields ?? 0) > 0 && (
                      <div className="bg-emerald-500/10 rounded-lg p-4 border border-emerald-500/30">
                        <p className="text-xs font-semibold text-emerald-400 uppercase mb-2">
                          Data Quality
                        </p>
                        <p className="text-3xl font-bold text-white mb-1">
                          {Math.round(
                            analytics.data_quality
                              ?.average_completion_percentage ?? 0,
                          )}
                          %
                        </p>
                        <p className="text-xs text-slate-400">
                          Average completion across{" "}
                          {analytics.data_quality?.total_fields ?? 0} fields
                        </p>
                      </div>
                    )}

                    {/* Recovery Status */}
                    {(analytics.recovery?.total_recovery_attempts ?? 0) > 0 && (
                      <div
                        className={`${analytics.recovery?.in_recovery ? "bg-amber-500/10 border-amber-500/30" : "bg-blue-500/10 border-blue-500/30"} rounded-lg p-4 border`}
                      >
                        <p
                          className={`text-xs font-semibold uppercase mb-2 ${analytics.recovery?.in_recovery ? "text-amber-400" : "text-blue-400"}`}
                        >
                          Recovery Status
                        </p>
                        <p className="text-2xl font-bold text-white mb-1 capitalize">
                          {(analytics.recovery?.status ?? "").replace("_", " ")}
                        </p>
                        <p className="text-xs text-slate-400">
                          {analytics.recovery?.total_recovery_attempts ?? 0} recovery
                          attempt(s)
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              )}
          </div>

          {/* Sidebar Stats */}
          <div className="space-y-4">
            {/* Product Statistics Summary */}
            {productStats?.statistics && (
              <div className="bg-gradient-to-br from-emerald-500/10 to-emerald-500/5 border border-emerald-500/30 rounded-xl p-5">
                <h3 className="text-sm font-semibold text-emerald-400 mb-4 flex items-center gap-2">
                  <BarChart3 className="w-4 h-4" />
                  Product Data
                </h3>

                <div className="space-y-3">
                  <div>
                    <p className="text-xs text-slate-400 mb-1">
                      Total Products
                    </p>
                    <p className="text-2xl font-bold text-white">
                      {(productStats.statistics.total_products ?? 0).toLocaleString()}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400 mb-1">Runs Ingested</p>
                    <p className="text-2xl font-bold text-emerald-400">
                      {productStats.statistics.total_runs_with_data}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400 mb-1">
                      Last Extraction
                    </p>
                    <p className="text-sm font-semibold text-slate-300">
                      {productStats.statistics.latest_extraction}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Quick Stats */}
            <div className="bg-gradient-to-br from-blue-500/10 to-blue-500/5 border border-blue-500/30 rounded-xl p-5">
              <h3 className="text-sm font-semibold text-blue-400 mb-4 flex items-center gap-2">
                <BarChart3 className="w-4 h-4" />
                Run Stats
              </h3>

              <div className="space-y-3">
                <div>
                  <p className="text-xs text-slate-400 mb-1">Total Runs</p>
                  <p className="text-2xl font-bold text-white">
                    {project.run_stats?.total_runs || 0}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-slate-400 mb-1">Pages Scraped</p>
                  <p className="text-2xl font-bold text-emerald-400">
                    {project.run_stats?.pages_scraped || 0}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-slate-400 mb-1">Success Rate</p>
                  <p className="text-2xl font-bold text-amber-400">
                    {project.run_stats && project.run_stats.total_runs > 0
                      ? `${project.run_stats.success_rate}%`
                      : project.run_stats?.total_runs === 0
                        ? "No runs yet"
                        : "—"}
                  </p>
                </div>
              </div>
            </div>

            {/* Advanced Analytics */}
            {analytics?.overview && analytics.overview.total_runs > 0 && (
              <div className="bg-gradient-to-br from-cyan-500/10 to-cyan-500/5 border border-cyan-500/30 rounded-xl p-5">
                <h3 className="text-sm font-semibold text-cyan-400 mb-4 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" />
                  Analytics Overview
                </h3>

                <div className="space-y-3">
                  <div>
                    <p className="text-xs text-slate-400 mb-1">Total Records</p>
                    <p className="text-2xl font-bold text-white">
                      {(analytics.overview.total_records_scraped ?? 0).toLocaleString()}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400 mb-1">
                      Completed Runs
                    </p>
                    <p className="text-2xl font-bold text-emerald-400">
                      {analytics.overview.completed_runs} /{" "}
                      {analytics.overview.total_runs}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400 mb-1">
                      Pages Analyzed
                    </p>
                    <p className="text-2xl font-bold text-cyan-400">
                      {(analytics.overview.total_pages_analyzed ?? 0).toLocaleString()}
                    </p>
                  </div>
                  {analytics.overview.progress_percentage > 0 && (
                    <div>
                      <p className="text-xs text-slate-400 mb-1">Progress</p>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 bg-slate-700 rounded-full h-2 overflow-hidden">
                          <div
                            className="bg-gradient-to-r from-cyan-500 to-blue-500 h-full rounded-full transition-all duration-300"
                            style={{
                              width: `${Math.min(analytics.overview.progress_percentage, 100)}%`,
                            }}
                          />
                        </div>
                        <span className="text-sm font-semibold text-cyan-400">
                          {Math.round(analytics.overview.progress_percentage)}%
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Performance Metrics */}
            {analytics?.performance && analytics.performance.items_per_minute > 0 && (
              <div className="bg-gradient-to-br from-amber-500/10 to-amber-500/5 border border-amber-500/30 rounded-xl p-5">
                <h3 className="text-sm font-semibold text-amber-400 mb-4 flex items-center gap-2">
                  <Clock className="w-4 h-4" />
                  Performance
                </h3>

                <div className="space-y-3">
                  <div>
                    <p className="text-xs text-slate-400 mb-1">Items/Minute</p>
                    <p className="text-2xl font-bold text-white">
                      {(analytics.performance.items_per_minute ?? 0).toFixed(2)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400 mb-1">Avg Duration</p>
                    <p className="text-lg font-semibold text-amber-400">
                      {Math.round(
                        (analytics.performance.average_run_duration_seconds ?? 0) / 60,
                      )}{" "}
                      min
                    </p>
                  </div>
                  {analytics.performance.estimated_total_items > 0 && (
                    <div>
                      <p className="text-xs text-slate-400 mb-1">
                        Est. Total Items
                      </p>
                      <p className="text-lg font-semibold text-slate-300">
                        {(analytics.performance.estimated_total_items ?? 0).toLocaleString()}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Metadata Stats */}
            {metadata && (
              <div className="bg-gradient-to-br from-purple-500/10 to-purple-500/5 border border-purple-500/30 rounded-xl p-5">
                <h3 className="text-sm font-semibold text-purple-400 mb-4 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" />
                  Metadata
                </h3>

                <div className="space-y-3">
                  {metadata.total_pages && (
                    <div>
                      <p className="text-xs text-slate-400 mb-1">Total Pages</p>
                      <p className="text-xl font-semibold text-white">
                        {metadata.total_pages}
                      </p>
                    </div>
                  )}
                  {metadata.total_products && (
                    <div>
                      <p className="text-xs text-slate-400 mb-1">
                        Total Products
                      </p>
                      <p className="text-xl font-semibold text-white">
                        {(metadata.total_products ?? 0).toLocaleString()}
                      </p>
                    </div>
                  )}
                  {metadata.status && (
                    <div>
                      <p className="text-xs text-slate-400 mb-1">Status</p>
                      <span
                        className={`inline-flex items-center px-2 py-1 rounded text-xs font-semibold border ${getStatusColor(metadata.status)}`}
                      >
                        {metadata.status}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Last Updated */}
            {lastUpdate && (
              <div className="text-center p-3 bg-slate-800/30 rounded-lg border border-slate-700/50">
                <p className="text-xs text-slate-400">
                  Last updated {lastUpdate.toLocaleTimeString()}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {showScheduler && (
        <SchedulerModal
          projectToken={token}
          onClose={() => setShowScheduler(false)}
          onSchedule={() => setShowScheduler(false)}
        />
      )}

      {showStatsModal && (
        <ColumnStatisticsModal
          token={token}
          title={projectName}
          isOpen={showStatsModal}
          onClose={() => setShowStatsModal(false)}
        />
      )}

      {showCSVModal && (
        <CSVDataModal
          token={token}
          title={projectName}
          isOpen={showCSVModal}
          onClose={() => setShowCSVModal(false)}
        />
      )}
    </div>
  );
}
