-- Video type per storyboard and render: 'flat' (the original 2D motion graphics) or '3d' (Scene 3D).
-- Existing rows are all 'flat', which is also the default, so nothing changes for old data.
ALTER TABLE storyboards ADD COLUMN video_type TEXT NOT NULL DEFAULT 'flat';
ALTER TABLE renders ADD COLUMN video_type TEXT NOT NULL DEFAULT 'flat';
