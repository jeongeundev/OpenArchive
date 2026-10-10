-- 042_share_folders_tables.sql — 폴더 단위 외부 공유 (#206)
CREATE TABLE share_folders (
  share_id   uuid NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
  folder_id  uuid NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (share_id, folder_id)
);

CREATE INDEX idx_share_folders_folder ON share_folders (folder_id);
