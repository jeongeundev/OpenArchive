import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyDocuments } from "./EmptyDocuments";

describe("EmptyDocuments", () => {
  it("업로드와 예제 명령을 로그인한 계정 이름으로 안내한다", () => {
    render(<EmptyDocuments username="admin" />);

    expect(screen.getByText("아직 문서가 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("openarchive demo --user admin")).toBeInTheDocument();
    expect(screen.getByText(/예제 64건/)).toBeInTheDocument();
    // 마크다운 백틱이 화면에 그대로 새지 않는다(#78 실사용 결함).
    expect(document.body).not.toHaveTextContent("`");
  });
});
