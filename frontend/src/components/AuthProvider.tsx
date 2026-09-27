"use client";

import { createContext, useContext, useEffect, useState } from "react";

import { getAuthStatus } from "@/lib/api";
import type { AuthStatus } from "@/lib/types";

const anonymous: AuthStatus = {
  authenticated: false,
  username: null,
  is_admin: false,
};

const AuthContext = createContext<{
  auth: AuthStatus;
  loading: boolean;
  setAuth: (auth: AuthStatus) => void;
}>({ auth: anonymous, loading: true, setAuth: () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const [auth, setAuth] = useState<AuthStatus>(anonymous);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    getAuthStatus(controller.signal)
      .then((status) => {
        if (!controller.signal.aborted) setAuth(status);
      })
      .catch(() => {
        if (!controller.signal.aborted) setAuth(anonymous);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  return <AuthContext value={{ auth, loading, setAuth }}>{children}</AuthContext>;
}

export function useAuth(): React.ContextType<typeof AuthContext> {
  return useContext(AuthContext);
}
