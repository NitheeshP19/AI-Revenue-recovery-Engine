package main

import (
	"log/slog"
	"math"
	"os"
	"time"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

// DB is the process-wide database connection pool.
// It is set exactly once in ConnectDatabase and never mutated afterwards.
var DB *gorm.DB

// ConnectDatabase establishes a connection to Neon PostgreSQL with exponential
// backoff to handle Neon's scale-to-zero cold-start delays.
//
// Retry schedule (base delay = 500ms, factor = 2):
//
//	Attempt 1 → immediate
//	Attempt 2 → wait  500ms
//	Attempt 3 → wait 1000ms
//	Attempt 4 → wait 2000ms
//	Attempt 5 → wait 4000ms → os.Exit(1) on failure
func ConnectDatabase() {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		slog.Error("DATABASE_URL environment variable is not set — cannot start")
		os.Exit(1)
	}

	const (
		maxAttempts = 5
		baseDelayMs = 500
	)

	gormCfg := &gorm.Config{
		// Suppress GORM's default logger; we use slog for all output.
		Logger: logger.Default.LogMode(logger.Silent),
		// Disable automatic created_at / updated_at — the DB triggers handle this.
		// (Set to false so GORM still writes them on Create, but doesn't override
		// the DB-side trigger on Update.)
		NowFunc: func() time.Time { return time.Now().UTC() },
	}

	var (
		db  *gorm.DB
		err error
	)

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		slog.Info("Attempting database connection",
			"attempt", attempt,
			"max_attempts", maxAttempts,
		)

		db, err = gorm.Open(postgres.Open(dsn), gormCfg)
		if err == nil {
			// Validate with a real ping — gorm.Open alone doesn't dial.
			sqlDB, sqlErr := db.DB()
			if sqlErr == nil {
				sqlErr = sqlDB.Ping()
			}

			if sqlErr == nil {
				// ── Connection pool tuning for Neon serverless ──────────────────
				// Neon bills per compute-second; keep idle connections minimal
				// while allowing bursts during webhook spikes.
				sqlDB.SetMaxIdleConns(2)
				sqlDB.SetMaxOpenConns(10)
				sqlDB.SetConnMaxLifetime(5 * time.Minute)
				sqlDB.SetConnMaxIdleTime(2 * time.Minute)

				slog.Info("Database connection established",
					"attempt", attempt,
					"driver", "postgres (Neon)",
				)
				DB = db
				return
			}
			err = sqlErr
		}

		if attempt == maxAttempts {
			slog.Error("Fatal: all database connection attempts exhausted",
				"attempts", maxAttempts,
				"last_error", err.Error(),
			)
			os.Exit(1)
		}

		// Exponential backoff: delay = baseDelayMs * 2^(attempt-1) milliseconds
		delay := time.Duration(math.Pow(2, float64(attempt-1))*baseDelayMs) * time.Millisecond
		slog.Warn("Database connection failed — retrying",
			"attempt", attempt,
			"error", err.Error(),
			"retry_in", delay.String(),
		)
		time.Sleep(delay)
	}
}

// GetDBStatus pings the database and returns true if it is reachable.
// Used by the /health endpoint to report live connectivity.
func GetDBStatus() bool {
	if DB == nil {
		return false
	}
	sqlDB, err := DB.DB()
	if err != nil {
		return false
	}
	return sqlDB.Ping() == nil
}

// CloseDatabase gracefully drains and closes the connection pool.
// Called during graceful shutdown to prevent connection leaks on Neon.
func CloseDatabase() {
	if DB == nil {
		return
	}
	sqlDB, err := DB.DB()
	if err != nil {
		slog.Error("Could not retrieve underlying sql.DB for closing", "error", err.Error())
		return
	}
	if closeErr := sqlDB.Close(); closeErr != nil {
		slog.Error("Error closing database connection pool", "error", closeErr.Error())
		return
	}
	slog.Info("Database connection pool closed gracefully")
}
