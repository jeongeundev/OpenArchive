// jest-dom matcher(toBeInTheDocument 등)를 vitest의 expect에 등록한다.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// globals: false 이므로 RTL의 자동 cleanup이 등록되지 않는다. 직접 건다.
afterEach(cleanup);
