"use client";
import apiClient from "@/lib/apiClient";
import { getApiHeaders } from "@/lib/apiBase";
import { readResponseData } from "@/lib/response";
import { useState, useEffect } from "react";
import { Activity, TrendingUp, Download, Zap, RefreshCw } from "lucide-react";
import Modal from "./Modal";

interface ProjectData {
  token: string;
  title: string;
  owner_email: string;
  last_run?: {
    status: string;
    pages: number;
    start_time: string;
    end_time?: string;
    run_token: string;
  } | null;
  analytics?: {
    overview?: {
      total_runs: number;
      completed_runs: number;
      total_records_scraped: number;
      progress_percentage: number;
    };
  };
}

interface AllProjectsAnalyticsModalProps {
  isOpen: boolean;
  onClose: () => void;
  projects: ProjectData[];
}

export default function AllProjectsAnalyticsModal({
  isOpen,
  onClose,
  projects,
}: AllProjectsAnalyticsModalProps) {
  const [projectsWithAnalytics, setProjectsWithAnalytics] = useState<
    ProjectData[]
  >([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"all" | "summary" | "progress">(
    "all",
  );
  const [runningProjects, setRunningProjects] = useState<ProjectData[]>([]);
  const [refreshInterval, setRefreshInterval] = useState<NodeJS.Timeout | null>(
    null,
  );

  useEffect(() => {
    if (isOpen && projects.length > 0) {
      fetchAllAnalytics();
      fetchRunningProjects();
    }
  }, [isOpen, projects]);

  // Auto-refresh progress for running projects every 3 seconds
  useEffect(() => {
    if (isOpen && activeTab === "progress") {
      const interval = setInterval(() => {
        fetchRunningProjects();
      }, 3000);
      setRefreshInterval(interval);
      return () => {
        clearInterval(interval);
      };
    }
    return void 0;
  }, [isOpen, activeTab]);

  // Clean up interval on unmount
  useEffect(() => {
    return () => {
      if (refreshInterval) clearInterval(refreshInterval);
    };
  }, [refreshInterval]);

  const fetchRunningProjects = async () => {
    try {
      // Request paginated projects (first 50 items for performance)
      const response = await apiClient.get("/api/projects?page=1&limit=50");
      if (response.status === 200) {
        const data = response.data;
        const running = (data.projects || []).filter(
          (p: ProjectData) => p.last_run?.status === "running",
        );
        setRunningProjects(running);
      }
    } catch (err) {
      console.warn("Failed to fetch running projects:", err);
    }
  };

  const fetchAllAnalytics = async () => {
    setLoading(true);
    try {
      const projectsData = await Promise.all(
        projects.map(async (project) => {
          try {
            const response = await fetch(
              `/api/analytics?token=${project.token}`,
              { headers: getApiHeaders() },
            );
            if (response.ok) {
              const analytics = await readResponseData<ProjectData["analytics"]>(
                response,
              );
              return {
                ...project,
                analytics,
              };
            }
            return project;
          } catch (err) {
            console.warn(
              `Failed to fetch analytics for ${project.token}:`,
              err,
            );
            return project;
          }
        }),
      );
      setProjectsWithAnalytics(projectsData);
      // Also fetch running projects on analytics load
      fetchRunningProjects();
    } finally {
      setLoading(false);
    }
  };

  const calculateTotalStats = () => {
    let totalProjects = projects.length;
    let totalRuns = 0;
    let totalRecords = 0;
    let completedRuns = 0;

    projectsWithAnalytics.forEach((project) => {
      if (project.analytics?.overview) {
        totalRuns += project.analytics.overview.total_runs || 0;
        totalRecords += project.analytics.overview.total_records_scraped || 0;
        completedRuns += project.analytics.overview.completed_runs || 0;
      }
    });

    return { totalProjects, totalRuns, totalRecords, completedRuns };
  };

  const downloadAnalyticsReport = () => {
    const stats = calculateTotalStats();
    const projectDetails = projectsWithAnalytics.map((p) => ({
      name: p.title,
      token: p.token,
      owner: p.owner_email,
      lastRun: p.last_run?.start_time,
      lastRunStatus: p.last_run?.status,
      pagesScraped: p.last_run?.pages,
      totalRecords: p.analytics?.overview?.total_records_scraped || 0,
      totalRuns: p.analytics?.overview?.total_runs || 0,
      completedRuns: p.analytics?.overview?.completed_runs || 0,
    }));

    const report = {
      timestamp: new Date().toISOString(),
      summary: stats,
      projects: projectDetails,
    };

    const dataStr = JSON.stringify(report, null, 2);
    const element = document.createElement("a");
    element.setAttribute(
      "href",
      "data:application/json;charset=utf-8," + encodeURIComponent(dataStr),
    );
    element.setAttribute(
      "download",
      `all-projects-analytics-${Date.now()}.json`,
    );
    element.style.display = "none";
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  if (!isOpen) return null;

  const stats = calculateTotalStats();

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="All Projects Analytics"
      size="xlarge"
    >
      <div className="space-y-4">
        {/* Tab Navigation */}
        <div className="flex gap-2 border-b border-slate-700/50 -mx-6 px-6 overflow-x-auto custom-scrollbar">
          <button
            onClick={() => setActiveTab("summary")}
            className={`py-3 px-4 font-medium border-b-2 transition-all duration-200 whitespace-nowrap ${
              activeTab === "summary"
                ? "border-blue-500 text-blue-400"
                : "border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600"
            }`}
          >
            Summary
          </button>
          <button
            onClick={() => setActiveTab("all")}
            className={`py-3 px-4 font-medium border-b-2 transition-all duration-200 whitespace-nowrap ${
              activeTab === "all"
                ? "border-blue-500 text-blue-400"
                : "border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600"
            }`}
          >
            All Projects ({projects.length})
          </button>
          <button
            onClick={() => setActiveTab("progress")}
            className={`py-3 px-4 font-medium border-b-2 transition-all duration-200 whitespace-nowrap flex items-center gap-1 ${
              activeTab === "progress"
                ? "border-blue-500 text-blue-400"
                : "border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600"
            }`}
          >
            <Zap className="w-4 h-4" />
            Progress ({runningProjects.length})
          </button>
        </div>

        {/* Summary Tab */}
        {activeTab === "summary" && (
          <div className="space-y-4">
            {/* Top Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="relative overflow-hidden bg-gradient-to-br from-blue-900/20 to-blue-800/10 backdrop-blur-sm border border-blue-500/30 rounded-lg p-4 hover:scale-105 transition-transform duration-200">
                <div className="absolute inset-0 bg-gradient-to-r from-blue-500/5 to-transparent"></div>
                <div className="relative">
                  <div className="flex items-center gap-2 mb-2">
                    <Activity className="w-4 h-4 text-blue-400" />
                    <p className="text-xs text-blue-300 font-medium">
                      Total Projects
                    </p>
                  </div>
                  <p className="text-3xl font-bold text-blue-100">
                    {stats.totalProjects}
                  </p>
                </div>
              </div>

              <div className="relative overflow-hidden bg-gradient-to-br from-emerald-900/20 to-emerald-800/10 backdrop-blur-sm border border-emerald-500/30 rounded-lg p-4 hover:scale-105 transition-transform duration-200">
                <div className="absolute inset-0 bg-gradient-to-r from-emerald-500/5 to-transparent"></div>
                <div className="relative">
                  <div className="flex items-center gap-2 mb-2">
                    <TrendingUp className="w-4 h-4 text-emerald-400" />
                    <p className="text-xs text-emerald-300 font-medium">
                      Total Runs
                    </p>
                  </div>
                  <p className="text-3xl font-bold text-emerald-100">
                    {stats.totalRuns}
                  </p>
                </div>
              </div>

              <div className="relative overflow-hidden bg-gradient-to-br from-purple-900/20 to-purple-800/10 backdrop-blur-sm border border-purple-500/30 rounded-lg p-4 hover:scale-105 transition-transform duration-200">
                <div className="absolute inset-0 bg-gradient-to-r from-purple-500/5 to-transparent"></div>
                <div className="relative">
                  <div className="flex items-center gap-2 mb-2">
                    <Activity className="w-4 h-4 text-purple-400" />
                    <p className="text-xs text-purple-300 font-medium">
                      Completed Runs
                    </p>
                  </div>
                  <p className="text-3xl font-bold text-purple-100">
                    {stats.completedRuns}
                  </p>
                </div>
              </div>

              <div className="relative overflow-hidden bg-gradient-to-br from-amber-900/20 to-amber-800/10 backdrop-blur-sm border border-amber-500/30 rounded-lg p-4 hover:scale-105 transition-transform duration-200">
                <div className="absolute inset-0 bg-gradient-to-r from-amber-500/5 to-transparent"></div>
                <div className="relative">
                  <div className="flex items-center gap-2 mb-2">
                    <TrendingUp className="w-4 h-4 text-amber-400" />
                    <p className="text-xs text-amber-300 font-medium">
                      Total Records
                    </p>
                  </div>
                  <p className="text-3xl font-bold text-amber-100">
                    {stats.totalRecords.toLocaleString()}
                  </p>
                </div>
              </div>
            </div>

            {/* Breakdown by Project */}
            <div className="mt-6">
              <h3 className="font-semibold text-slate-200 mb-3">
                Records Per Project
              </h3>
              <div className="space-y-2 max-h-60 overflow-y-auto custom-scrollbar">
                {projectsWithAnalytics
                  .sort(
                    (a, b) =>
                      (b.analytics?.overview?.total_records_scraped || 0) -
                      (a.analytics?.overview?.total_records_scraped || 0),
                  )
                  .map((project) => {
                    const recordCount =
                      project.analytics?.overview?.total_records_scraped || 0;
                    const percentage =
                      stats.totalRecords > 0
                        ? (recordCount / stats.totalRecords) * 100
                        : 0;

                    return (
                      <div
                        key={project.token}
                        className="flex items-center gap-3 p-2 rounded-lg bg-slate-800/30 hover:bg-slate-800/50 transition-colors"
                      >
                        <div className="flex-1">
                          <div className="flex justify-between mb-1">
                            <span className="text-sm font-medium text-slate-300 truncate">
                              {project.title}
                            </span>
                            <span className="text-sm font-bold text-slate-100 font-mono">
                              {recordCount.toLocaleString()}
                            </span>
                          </div>
                          <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                            <div
                              className="bg-gradient-to-r from-blue-600 to-cyan-500 h-2 rounded-full transition-all duration-500"
                              style={{ width: `${percentage}%` }}
                            ></div>
                          </div>
                        </div>
                        <span className="text-xs text-slate-400 min-w-12 text-right font-mono">
                          {percentage.toFixed(1)}%
                        </span>
                      </div>
                    );
                  })}
              </div>
            </div>

            <button
              onClick={downloadAnalyticsReport}
              className="w-full mt-4 flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-emerald-600 to-emerald-500 hover:from-emerald-500 hover:to-emerald-400 text-white rounded-lg font-medium transition-all duration-200 shadow-lg shadow-emerald-500/20 hover:shadow-emerald-400/30 hover:scale-105"
            >
              <Download className="w-4 h-4" />
              Download Full Report
            </button>
          </div>
        )}

        {/* All Projects Tab */}
        {activeTab === "all" && (
          <div className="space-y-3 max-h-96 overflow-y-auto custom-scrollbar">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <div className="relative">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
                  <div className="absolute inset-0 blur-xl bg-blue-500/20 animate-pulse"></div>
                </div>
              </div>
            ) : projectsWithAnalytics.length === 0 ? (
              <div className="text-center py-8 text-slate-400">
                No projects available
              </div>
            ) : (
              projectsWithAnalytics.map((project) => {
                const isRunning = runningProjects.some(
                  (rp) => rp.token === project.token,
                );
                return (
                  <div
                    key={project.token}
                    className={`relative overflow-hidden border rounded-lg p-4 transition-all duration-200 backdrop-blur-sm ${
                      isRunning
                        ? "bg-gradient-to-br from-amber-900/20 to-amber-800/10 border-amber-500/30 hover:border-amber-400/50"
                        : "bg-slate-800/30 border-slate-700/50 hover:border-slate-600"
                    }`}
                  >
                    {isRunning && (
                      <div className="absolute inset-0 bg-gradient-to-r from-amber-500/5 to-transparent"></div>
                    )}
                    <div className="relative">
                      <div className="flex items-start justify-between mb-2">
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <h4 className="font-semibold text-slate-200">
                              {project.title}
                            </h4>
                            {isRunning && (
                              <span className="flex items-center gap-1 px-2 py-1 bg-gradient-to-r from-amber-600 to-amber-500 text-white rounded-full text-xs font-medium shadow-lg shadow-amber-500/20">
                                <Zap className="w-3 h-3 animate-pulse" />
                                RUNNING
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-slate-400 mt-1">
                            Owner: {project.owner_email}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-sm font-bold text-blue-400 font-mono">
                            {(
                              project.analytics?.overview
                                ?.total_records_scraped || 0
                            ).toLocaleString()}
                          </p>
                          <p className="text-xs text-slate-400">Records</p>
                        </div>
                      </div>

                      <div className="grid grid-cols-3 gap-2 mt-3 text-xs">
                        <div className="bg-slate-900/50 p-2 rounded border border-slate-700/50 backdrop-blur-sm">
                          <p className="text-slate-400">Total Runs</p>
                          <p className="font-bold text-slate-200">
                            {project.analytics?.overview?.total_runs || 0}
                          </p>
                        </div>
                        <div className="bg-slate-900/50 p-2 rounded border border-slate-700/50 backdrop-blur-sm">
                          <p className="text-slate-400">Completed</p>
                          <p className="font-bold text-emerald-400">
                            {project.analytics?.overview?.completed_runs || 0}
                          </p>
                        </div>
                        <div className="bg-slate-900/50 p-2 rounded border border-slate-700/50 backdrop-blur-sm">
                          <p className="text-slate-400">Last Status</p>
                          <p
                            className={`font-bold capitalize ${
                              isRunning ? "text-amber-400" : "text-slate-200"
                            }`}
                          >
                            {isRunning
                              ? `Running (${project.last_run?.pages || 0} pages)`
                              : project.last_run?.status || "N/A"}
                          </p>
                        </div>
                      </div>

                      {project.last_run?.start_time && (
                        <p className="text-xs text-slate-500 mt-2">
                          Last run:{" "}
                          {new Date(
                            project.last_run.start_time,
                          ).toLocaleDateString()}{" "}
                          at{" "}
                          {new Date(
                            project.last_run.start_time,
                          ).toLocaleTimeString()}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* Progress Tab - Real-time Running Projects */}
        {activeTab === "progress" && (
          <div className="space-y-3 max-h-96 overflow-y-auto custom-scrollbar">
            <div className="flex items-center justify-between mb-4 p-3 bg-slate-800/30 rounded-lg border border-slate-700/50">
              <div>
                <p className="text-sm font-semibold text-slate-200">
                  {runningProjects.length === 0
                    ? "No projects running"
                    : `${runningProjects.length} project${runningProjects.length !== 1 ? "s" : ""} running`}
                </p>
                <p className="text-xs text-slate-400 mt-1">
                  Page counts update every 3 seconds
                </p>
              </div>
              <button
                onClick={fetchRunningProjects}
                className="p-2 hover:bg-slate-700/50 rounded-lg transition-all duration-200 group"
                title="Refresh now"
              >
                <RefreshCw className="w-4 h-4 text-blue-400 group-hover:rotate-180 transition-transform duration-500" />
              </button>
            </div>

            {runningProjects.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <div className="relative inline-block">
                  <Zap className="w-16 h-16 text-slate-600 mb-3" />
                  <div className="absolute inset-0 blur-2xl bg-slate-500/10"></div>
                </div>
                <p className="text-slate-400 font-medium text-lg">
                  No projects running
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  Projects will appear here when they start running
                </p>
              </div>
            ) : (
              runningProjects.map((project) => {
                const pages = project.last_run?.pages || 0;
                const estimatedPages = Math.max(pages, 10); // Use page count as estimate
                const progressPercent = Math.min(
                  (pages / estimatedPages) * 100,
                  95,
                ); // Cap at 95% until completed

                return (
                  <div
                    key={project.token}
                    className="relative overflow-hidden bg-gradient-to-br from-amber-900/20 to-amber-800/10 backdrop-blur-sm border border-amber-500/30 rounded-lg p-4 hover:border-amber-400/50 transition-all duration-200"
                  >
                    <div className="absolute inset-0 bg-gradient-to-r from-amber-500/5 to-transparent"></div>
                    <div className="relative">
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <Zap className="w-4 h-4 text-amber-400 animate-pulse" />
                            <h4 className="font-semibold text-slate-200">
                              {project.title}
                            </h4>
                          </div>
                          <p className="text-xs text-slate-400 font-mono">
                            Token: {project.token.substring(0, 8)}...
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-sm font-bold text-amber-400 font-mono">
                            {pages.toLocaleString()}
                          </p>
                          <p className="text-xs text-slate-400">
                            pages scraped
                          </p>
                        </div>
                      </div>

                      {/* Progress Bar */}
                      <div className="space-y-1">
                        <div className="w-full bg-amber-900/30 rounded-full h-3 overflow-hidden">
                          <div
                            className="bg-gradient-to-r from-amber-500 to-amber-400 h-3 rounded-full transition-all duration-500 shadow-lg shadow-amber-500/30"
                            style={{ width: `${progressPercent}%` }}
                          ></div>
                        </div>
                        <p className="text-xs text-slate-400 text-right font-mono">
                          {progressPercent.toFixed(0)}% in progress
                        </p>
                      </div>

                      {/* Start Time */}
                      {project.last_run?.start_time && (
                        <p className="text-xs text-slate-500 mt-2">
                          Started:{" "}
                          {new Date(
                            project.last_run.start_time,
                          ).toLocaleTimeString()}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
