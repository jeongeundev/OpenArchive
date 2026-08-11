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
    let active = true;
    getAuthStatus()
      .then((status) => {
        if (active) setAuth(status);
      })
      .catch(() => {
        if (active) setAuth(anonymous);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return <AuthContext value={{ auth, loading, setAuth }}>{children}</AuthContext>;
}

export function useAuth(): React.ContextType<typeof AuthContext> {
  return useContext(AuthContext);
}
