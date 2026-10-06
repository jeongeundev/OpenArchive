import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useFolders } from "./useFolders";
afterEach(() => vi.unstubAllGlobals());
it("loads and refreshes folders, aborting on cleanup", async () => {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify([{id: "f"}]))));
  vi.stubGlobal("fetch", fetchMock);
  const {result, unmount} = renderHook(() => useFolders());
  expect(result.current.loading).toBe(true);
  await waitFor(() => expect(result.current.folders).toEqual([{id: "f"}]));
  expect(result.current.loading).toBe(false);
  act(() => result.current.refresh());
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(fetchMock.mock.calls[0][0]).toBe("/api/folders");
  unmount();
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
});
it("does not request while disabled and starts when enabled", async () => {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response("[]")));
  vi.stubGlobal("fetch", fetchMock);
  const {result, rerender} = renderHook(({enabled}) => useFolders(enabled), {initialProps: {enabled: false}});
  act(() => result.current.refresh());
  expect(fetchMock).not.toHaveBeenCalled();
  expect(result.current.loading).toBe(false);
  rerender({enabled: true});
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
});
it("returns server denial as an error value and clears it after refresh", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({detail: "권한이 없습니다."}), {status: 403})).mockImplementation(() => Promise.resolve(new Response("[]")));
  vi.stubGlobal("fetch", fetchMock);
  const {result} = renderHook(() => useFolders());
  await waitFor(() => expect(result.current.error).toBe("권한이 없습니다."));
  expect(result.current.loading).toBe(false);
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.error).toBeNull());
});
