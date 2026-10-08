import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, getCurrentUser, logoutAccount, type CurrentUser } from "../../api/client";

export type AuthConfirmation = { user: CurrentUser; epoch: number };

type AuthContextValue = {
  currentUser: CurrentUser | null;
  isAdmin: boolean;
  isLoading: boolean;
  refreshAuth: (expectedEpoch?: number) => Promise<AuthConfirmation | null>;
  isCurrentAuthSession: (confirmation: AuthConfirmation) => boolean;
  logout: () => void;
  authSessionEpoch: number;
  updateCurrentUserForAuthSession: (user: CurrentUser, requestAuthSessionEpoch: number) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function updateCachedCurrentUserForActiveSession(
  queryClient: QueryClient,
  updatedUser: CurrentUser,
  requestAuthSessionEpoch: number,
  currentAuthSessionEpoch: number,
) {
  if (requestAuthSessionEpoch !== currentAuthSessionEpoch) return;
  queryClient.setQueryData<CurrentUser | null>(["auth", "me"], (currentUser) =>
    currentUser?.id === updatedUser.id ? updatedUser : currentUser,
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const authSessionEpochRef = useRef(0);
  const [authSessionEpoch, setAuthSessionEpoch] = useState(0);
  const advanceAuthSessionEpoch = useCallback(() => {
    const nextAuthSessionEpoch = authSessionEpochRef.current + 1;
    authSessionEpochRef.current = nextAuthSessionEpoch;
    setAuthSessionEpoch(nextAuthSessionEpoch);
  }, []);
  const query = useQuery({
    queryKey: ["auth", "me"],
    queryFn: async ({ signal }) => {
      try {
        return await getCurrentUser(signal);
      } catch (error) {
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return null;
        throw error;
      }
    },
    retry: false,
  });
  const logoutMutation = useMutation({
    mutationFn: (_sessionEpoch: number) => logoutAccount(),
    onSuccess: (_result, requestSessionEpoch) => {
      if (requestSessionEpoch !== authSessionEpochRef.current) return;
      void queryClient.cancelQueries({ queryKey: ["auth", "me"], exact: true });
      queryClient.setQueryData(["auth", "me"], null);
      void queryClient.invalidateQueries();
    },
    // Drop every in-memory cache and open dialog: a clean reload is the only state that cannot leak the previous account.
    onSettled: () => { window.location.reload(); },
  });
  const currentUser = query.data ?? null;
  const isCurrentAuthSession = useCallback((confirmation: AuthConfirmation) =>
    confirmation.epoch === authSessionEpochRef.current &&
    queryClient.getQueryData<CurrentUser | null>(["auth", "me"])?.id === confirmation.user.id,
  [queryClient]);
  const refreshAuth = useCallback(async (expectedEpoch?: number): Promise<AuthConfirmation | null> => {
    const requestSessionEpoch = expectedEpoch ?? authSessionEpochRef.current;
    if (requestSessionEpoch !== authSessionEpochRef.current) return null;
    await queryClient.cancelQueries({ queryKey: ["auth", "me"], exact: true });
    if (requestSessionEpoch !== authSessionEpochRef.current) return null;
    const result = await query.refetch();
    if (!result.isSuccess || !result.data || requestSessionEpoch !== authSessionEpochRef.current) return null;
    advanceAuthSessionEpoch();
    return { user: result.data, epoch: authSessionEpochRef.current };
  }, [advanceAuthSessionEpoch, query.refetch, queryClient]);
  const logout = useCallback(() => {
    advanceAuthSessionEpoch();
    void queryClient.cancelQueries({ queryKey: ["auth", "me"], exact: true });
    queryClient.setQueryData(["auth", "me"], null);
    logoutMutation.mutate(authSessionEpochRef.current);
  }, [advanceAuthSessionEpoch, logoutMutation.mutate, queryClient]);
  const updateCurrentUserForAuthSession = useCallback((updatedUser: CurrentUser, requestAuthSessionEpoch: number) => {
    updateCachedCurrentUserForActiveSession(
      queryClient,
      updatedUser,
      requestAuthSessionEpoch,
      authSessionEpochRef.current,
    );
  }, [queryClient]);
  return (
    <AuthContext.Provider
      value={{
        currentUser,
        isAdmin: currentUser?.role === "admin",
        isLoading: query.isLoading,
        refreshAuth,
        isCurrentAuthSession,
        logout,
        authSessionEpoch,
        updateCurrentUserForAuthSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
