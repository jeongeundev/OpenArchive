const STORAGE_KEY = "openarchive.demo-user";

export const DEMO_USERS: readonly string[] = ["alice", "bob"];

export function getCurrentUser(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  const storedUser = window.localStorage.getItem(STORAGE_KEY);
  return storedUser !== null && DEMO_USERS.includes(storedUser) ? storedUser : null;
}

export function setCurrentUser(user: string | null): void {
  if (user === null) {
    window.localStorage.removeItem(STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(STORAGE_KEY, user);
}
