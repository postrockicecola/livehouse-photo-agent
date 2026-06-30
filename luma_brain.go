package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	_ "embed"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

//go:embed luma_brain_schema.sql
var lumaBrainSchemaSQL string

// PhotoStatus matches luma_brain_schema.sql CHECK constraint.
type PhotoStatus string

const (
	StatusNew       PhotoStatus = "NEW"
	StatusIngested  PhotoStatus = "INGESTED"
	// StatusAnalyzing is legacy-only in DB CHECK; execution claims live in jobs.status (see Python executor).
	StatusAnalyzing PhotoStatus = "ANALYZING"
	StatusAnalyzed  PhotoStatus = "ANALYZED"
)

// PhotoRow mirrors photos table — used by ingest worker and future tooling.
type PhotoRow struct {
	ID        int64
	FileHash  string
	FilePath  string
	DeviceID  string
	Status    PhotoStatus
	SessionID sql.NullInt64
	CreatedAt int64
	UpdatedAt sql.NullInt64
	VectorRef sql.NullString
}

// SessionRow mirrors sessions table — one row per archive folder batch.
type SessionRow struct {
	ID          int64
	SessionKey  string
	SessionDir  string
	ArchiveRoot string
	DeviceID    string
	RawDir      string
	PreviewsDir string
	PhotoCount  int
	StartedAt   int64
	ClosedAt    sql.NullInt64
	Notes       sql.NullString
}

// OpenLumaBrain opens SQLite with sane pragmas and applies schema if needed.
func OpenLumaBrain(path string) (*sql.DB, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return nil, fmt.Errorf("brain db path empty")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	dsn := fmt.Sprintf("file:%s?_busy_timeout=8000&_journal_mode=WAL&_foreign_keys=1", abs)
	db, err := sql.Open("sqlite3", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetConnMaxLifetime(time.Hour)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		_ = db.Close()
		return nil, err
	}
	if _, err := db.ExecContext(ctx, lumaBrainSchemaSQL); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("apply schema: %w", err)
	}
	return db, nil
}

// FingerprintARW builds a stable short hash from name + size + mtime (no full file read).
func FingerprintARW(baseName string, size int64, modUnix int64) string {
	key := fmt.Sprintf("%s|%d|%d", strings.ToUpper(strings.TrimSpace(baseName)), size, modUnix)
	sum := sha256.Sum256([]byte(key))
	return hex.EncodeToString(sum[:])
}

func photoStatusShouldSkipIngest(status string) bool {
	switch status {
	case string(StatusIngested), string(StatusAnalyzing), string(StatusAnalyzed):
		return true
	default:
		return false
	}
}

// LookupPhotoStatus returns (status, true) if row exists.
func LookupPhotoStatus(db *sql.DB, fileHash string) (string, bool, error) {
	var st string
	err := db.QueryRow(`SELECT status FROM photos WHERE file_hash = ?`, fileHash).Scan(&st)
	if err == sql.ErrNoRows {
		return "", false, nil
	}
	if err != nil {
		return "", false, err
	}
	return st, true, nil
}

// GetOrCreateSession returns session id for (session_key, device_id).
func GetOrCreateSession(db *sql.DB, archiveRoot, deviceID, sessionKey, sessionDir, rawDir, previewsDir string) (int64, error) {
	now := time.Now().Unix()
	tx, err := db.Begin()
	if err != nil {
		return 0, err
	}
	defer func() { _ = tx.Rollback() }()

	var id int64
	err = tx.QueryRow(
		`SELECT id FROM sessions WHERE session_key = ? AND device_id = ?`,
		sessionKey, deviceID,
	).Scan(&id)
	if err == nil {
		return id, tx.Commit()
	}
	if err != sql.ErrNoRows {
		return 0, err
	}
	res, err := tx.Exec(
		`INSERT INTO sessions (session_key, session_dir, archive_root, device_id, raw_dir, previews_dir, started_at)
		 VALUES (?,?,?,?,?,?,?)`,
		sessionKey, sessionDir, archiveRoot, deviceID, rawDir, previewsDir, now,
	)
	if err != nil {
		return 0, err
	}
	id, err = res.LastInsertId()
	if err != nil {
		return 0, err
	}
	return id, tx.Commit()
}

// IngestNewARW copies src into archive then records NEW→INGESTED in the DB (rolled back on failure).
func IngestNewARW(db *sql.DB, fileHash, destPath, deviceID string, sessionID int64, srcPath string) error {
	now := time.Now().Unix()
	if err := os.MkdirAll(filepath.Dir(destPath), 0755); err != nil {
		return err
	}
	tmp := destPath + ".partial"
	_ = os.Remove(tmp)
	if err := copyFileAtomic(srcPath, tmp, destPath); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	tx, err := db.Begin()
	if err != nil {
		_ = os.Remove(destPath)
		return err
	}
	defer func() { _ = tx.Rollback() }()

	res, err := tx.Exec(
		`INSERT INTO photos (file_hash, file_path, device_id, status, session_id, created_at, updated_at)
		 VALUES (?,?,?,?,?,?,?)`,
		fileHash, destPath, deviceID, string(StatusNew), sessionID, now, now,
	)
	if err != nil {
		_ = os.Remove(destPath)
		return err
	}
	pid, err := res.LastInsertId()
	if err != nil {
		_ = os.Remove(destPath)
		return err
	}
	_, err = tx.Exec(
		`UPDATE photos SET status = ?, updated_at = ? WHERE id = ?`,
		string(StatusIngested), now, pid,
	)
	if err != nil {
		_ = os.Remove(destPath)
		return err
	}
	if _, err = tx.Exec(`UPDATE sessions SET photo_count = photo_count + 1 WHERE id = ?`, sessionID); err != nil {
		_ = os.Remove(destPath)
		return err
	}
	return tx.Commit()
}

// EnsureIngestedRow inserts an INGESTED row if missing (repair archive ↔ DB drift). Returns true if a row was inserted.
func EnsureIngestedRow(db *sql.DB, fileHash, destPath, deviceID string, sessionID int64) (bool, error) {
	now := time.Now().Unix()
	res, err := db.Exec(
		`INSERT OR IGNORE INTO photos (file_hash, file_path, device_id, status, session_id, created_at, updated_at)
		 VALUES (?,?,?,?,?,?,?)`,
		fileHash, destPath, deviceID, string(StatusIngested), sessionID, now, now,
	)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

func copyFileAtomic(src, tmp, dest string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := out.Sync(); err != nil {
		_ = out.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := out.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, dest); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}
