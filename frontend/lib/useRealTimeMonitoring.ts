import apiClient from "@/lib/apiClient";
import { getApiHeaders } from "@/lib/apiBase";
import { getResponseMessage, readResponseData } from "@/lib/response";
import { useState, useEffect, useCallback, useRef } from 'react';

interface MonitoringSession {
  sessionId: number;
  projectId: number;
  runToken: string;
  targetPages: number;
  status: 'active' | 'completed' | 'failed' | 'cancelled';
  totalPages: number;
  totalRecords: number;
  progressPercentage: number;
  currentUrl?: string;
  errorMessage?: string;
  startTime: string;
  endTime?: string;
}

interface ScrapedRecord {
  id: number;
  pageNumber: number;
  data: Record<string, any>;
  createdAt: string;
}

interface UseRealTimeMonitoringReturn {
  // State
  session: MonitoringSession | null;
  data: ScrapedRecord[];
  isMonitoring: boolean;
  error: string | null;
  lastUpdated: Date | null;
  
  // Actions
  startMonitoring: (projectToken: string, runToken: string, pages: number) => Promise<void>;
  stopMonitoring: () => Promise<void>;
  fetchMoreData: () => Promise<void>;
  refresh: () => Promise<void>;
  clearError: () => void;
  
  // Statistics
  recordCount: number;
  pageCount: number;
  progress: number;
}

/**
 * Custom hook for real-time monitoring of ParseHub runs
 * Polls status every 2 seconds and data every 3 seconds
 */
export function useRealTimeMonitoring(): UseRealTimeMonitoringReturn {
  // State
  const [session, setSession] = useState<MonitoringSession | null>(null);
  const [data, setData] = useState<ScrapedRecord[]>([]);
  const [isMonitoring, setIsMonitoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Refs for managing polling intervals
  const statusIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const dataIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const dataOffsetRef = useRef(0);

  /**
   * Start real-time monitoring of a run
   */
  const startMonitoring = useCallback(
    async (projectToken: string, runToken: string, pages: number) => {
      try {
        setError(null);
        setIsMonitoring(true);
        setData([]);
        dataOffsetRef.current = 0;

        // Call frontend API to start monitoring
        const response = await fetch('/api/monitor/start', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...getApiHeaders(),
          },
          body: JSON.stringify({
            projectToken,
            runToken,
            pages,
          }),
        });

        const result = await readResponseData<Record<string, any>>(response);

        if (!response.ok) {
          throw new Error(getResponseMessage(result, 'Failed to start monitoring'));
        }

        if (!result.success && !result.sessionId) {
          throw new Error(result.error || 'Failed to start monitoring');
        }

        // Initialize session state
        setSession({
          sessionId: result.sessionId,
          projectId: 0, // Will be updated on first status fetch
          runToken,
          targetPages: pages,
          status: 'active',
          totalPages: 0,
          totalRecords: 0,
          progressPercentage: 0,
          startTime: result.startedAt,
        });

        // Start polling for status (every 2 seconds)
        statusIntervalRef.current = setInterval(() => {
          fetchStatus(result.sessionId);
        }, 2000);

        // Start polling for data (every 3 seconds)
        dataIntervalRef.current = setInterval(() => {
          fetchData(result.sessionId);
        }, 3000);

        // Initial fetch
        await fetchStatus(result.sessionId);
        await fetchData(result.sessionId);
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Unknown error occurred';
        setError(errorMessage);
        setIsMonitoring(false);
        logger.error('Error starting monitoring:', err);
      }
    },
    []
  );

  /**
   * Fetch current monitoring status
   */
  const fetchStatus = useCallback(
    async (sessionId: number) => {
      try {
        const response = await fetch(`/api/monitor/status?sessionId=${sessionId}`, {
          headers: getApiHeaders(),
        });

        const result = await readResponseData<Record<string, any>>(response);

        if (!response.ok) {
          throw new Error(getResponseMessage(result, 'Failed to fetch status'));
        }

        setSession((prev) => {
          if (!prev) return null;
          return {
            ...prev,
            status: result.status,
            totalPages: result.totalPages ?? prev.totalPages,
            totalRecords: result.totalRecords ?? prev.totalRecords,
            progressPercentage: result.progressPercentage ?? prev.progressPercentage,
            currentUrl: result.currentUrl,
            errorMessage: result.errorMessage,
            endTime: result.endTime,
          };
        });

        setLastUpdated(new Date());

        // Stop monitoring if session is complete or failed
        if (result.status === 'completed' || result.status === 'failed' || result.status === 'cancelled') {
          await stopMonitoring();
        }
      } catch (err) {
        logger.error('Error fetching status:', err);
      }
    },
    []
  );

  /**
   * Fetch scraped records
   */
  const fetchData = useCallback(
    async (sessionId: number) => {
      try {
        const response = await fetch(
          `/api/monitor/data?sessionId=${sessionId}&limit=100&offset=${dataOffsetRef.current}`,
          { headers: getApiHeaders() }
        );

        const result = await readResponseData<Record<string, any>>(response);

        if (!response.ok) {
          throw new Error(getResponseMessage(result, 'Failed to fetch data'));
        }

        // Add new records (avoiding duplicates)
        setData((prev) => {
          const existingIds = new Set(prev.map((r) => r.id));
          const newRecords = result.records.filter((r: ScrapedRecord) => !existingIds.has(r.id));

          return [...prev, ...newRecords];
        });

        // Update offset for pagination
        dataOffsetRef.current = result.offset + result.records.length;

        setLastUpdated(new Date());
      } catch (err) {
        logger.error('Error fetching data:', err);
      }
    },
    []
  );

  /**
   * Stop monitoring
   */
  const stopMonitoring = useCallback(async () => {
    try {
      if (!session?.sessionId) return;

      // Clear intervals
      if (statusIntervalRef.current) clearInterval(statusIntervalRef.current);
      if (dataIntervalRef.current) clearInterval(dataIntervalRef.current);
      statusIntervalRef.current = null;
      dataIntervalRef.current = null;

      // Call backend to stop monitoring
      await fetch('/api/monitor/stop', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getApiHeaders(),
        },
        body: JSON.stringify({
          sessionId: session.sessionId,
        }),
      }).catch((err) => {
        logger.error('Error stopping monitoring:', err);
      });

      setIsMonitoring(false);
    } catch (err) {
      logger.error('Error in stopMonitoring:', err);
      setIsMonitoring(false);
    }
  }, [session?.sessionId]);

  /**
   * Fetch more data (for pagination)
   */
  const fetchMoreData = useCallback(async () => {
    if (!session?.sessionId) return;
    await fetchData(session.sessionId);
  }, [session?.sessionId, fetchData]);

  /**
   * Manually refresh status and data
   */
  const refresh = useCallback(async () => {
    if (!session?.sessionId) return;
    await fetchStatus(session.sessionId);
    await fetchData(session.sessionId);
  }, [session?.sessionId, fetchStatus, fetchData]);

  /**
   * Clear error state
   */
  const clearError = useCallback(() => {
    setError(null);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (statusIntervalRef.current) clearInterval(statusIntervalRef.current);
      if (dataIntervalRef.current) clearInterval(dataIntervalRef.current);
    };
  }, []);

  return {
    // State
    session,
    data,
    isMonitoring,
    error,
    lastUpdated,

    // Actions
    startMonitoring,
    stopMonitoring,
    fetchMoreData,
    refresh,
    clearError,

    // Statistics
    recordCount: data.length,
    pageCount: session?.totalPages ?? 0,
    progress: session?.progressPercentage ?? 0,
  };
}

// Logger utility
const logger = {
  error: (message: string, error?: any) => {
    console.error(`[useRealTimeMonitoring] ${message}`, error);
  },
  info: (message: string) => {
    console.log(`[useRealTimeMonitoring] ${message}`);
  },
  warn: (message: string) => {
    console.warn(`[useRealTimeMonitoring] ${message}`);
  },
};
