package main

import (
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/cors"
	"github.com/gofiber/fiber/v2/middleware/recover"
	"github.com/joho/godotenv"
)

func main() {
	// ── 1. Structured JSON logger ──────────────────────────────────────────────
	// slog with JSON handler writes machine-parseable logs suitable for
	// ingestion by Datadog, Google Cloud Logging, or any structured log pipeline.
	slogHandler := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level:     slog.LevelInfo,
		AddSource: false, // set to true for debug builds
	})
	slog.SetDefault(slog.New(slogHandler))

	slog.Info("AI Revenue Recovery — Go Ingestion API initialising",
		"version", "1.0.0",
		"service", "go-ingestion-api",
		"pid", os.Getpid(),
	)

	// ── 2. Load environment variables ──────────────────────────────────────────
	// godotenv.Load is a no-op if .env is missing — production environments
	// supply vars through the platform (Railway, Render, Fly.io, etc.).
	if err := godotenv.Load(); err != nil {
		slog.Warn(".env file not found — reading from system environment",
			"detail", err.Error(),
		)
	}

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	appEnv := os.Getenv("APP_ENV")
	if appEnv == "" {
		appEnv = "development"
	}

	slog.Info("Configuration loaded",
		"port", port,
		"environment", appEnv,
	)

	// ── 3. Connect to Neon PostgreSQL (exponential backoff) ───────────────────
	slog.Info("Initiating database connection with exponential backoff...")
	ConnectDatabase()
	slog.Info("Database ready — connection pool active")

	// ── 4. Initialise Fiber ───────────────────────────────────────────────────
	app := fiber.New(fiber.Config{
		AppName:      "AI Revenue Recovery API v1.0.0",
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,

		// Disable Fiber's default HTML error page — every error is JSON.
		ErrorHandler: func(c *fiber.Ctx, err error) error {
			slog.Error("Unhandled fiber error",
				"error", err.Error(),
				"method", c.Method(),
				"path", c.Path(),
			)
			code := fiber.StatusInternalServerError
			if e, ok := err.(*fiber.Error); ok {
				code = e.Code
			}
			return c.Status(code).JSON(ErrorResponse{
				Status:  "error",
				Error:   "INTERNAL_SERVER_ERROR",
				Details: "An unexpected error occurred. Please contact support.",
			})
		},
	})

	// ── 5. Global Middleware ───────────────────────────────────────────────────

	// Recover — converts any panic in a handler into a 500 response instead of
	// crashing the process. Essential for production stability.
	app.Use(recover.New(recover.Config{
		EnableStackTrace: appEnv != "production", // expose stack in non-prod only
		StackTraceHandler: func(c *fiber.Ctx, e interface{}) {
			slog.Error("Panic recovered",
				"panic", e,
				"path", c.Path(),
				"method", c.Method(),
			)
		},
	}))

	// CORS — permits the React dashboard (running on a different origin) to call
	// this API. In production, restrict AllowOrigins to your actual dashboard URL.
	allowedOrigins := os.Getenv("ALLOWED_ORIGINS")
	if allowedOrigins == "" {
		allowedOrigins = "*" // open in dev; always restrict in production
		if appEnv == "production" {
			slog.Warn("ALLOWED_ORIGINS is not set — CORS is open (*). Set this env var in production.")
		}
	}
	app.Use(cors.New(cors.Config{
		AllowOrigins:     allowedOrigins,
		AllowMethods:     "GET,POST,PUT,PATCH,DELETE,OPTIONS",
		AllowHeaders:     "Origin,Content-Type,Accept,Authorization,X-Correlation-ID,X-Request-ID",
		ExposeHeaders:    "X-Correlation-ID",
		AllowCredentials: false,
		MaxAge:           86400, // pre-flight cache: 24 hours
	}))

	// Request logger — emits one structured log line per HTTP request.
	// Placed after CORS/Recover so their overhead is also captured.
	app.Use(func(c *fiber.Ctx) error {
		start := time.Now()
		chainErr := c.Next()
		slog.Info("http_request",
			"method", c.Method(),
			"path", c.Path(),
			"status", c.Response().StatusCode(),
			"latency_ms", time.Since(start).Milliseconds(),
			"bytes_out", len(c.Response().Body()),
			"remote_ip", c.IP(),
			"correlation_id", c.Get("X-Correlation-ID", "-"),
		)
		return chainErr
	})

	// ── 6. Route Registration ──────────────────────────────────────────────────

	// Health check — no auth required, used by load balancers and monitoring.
	app.Get("/health", HealthHandler)

	// v1 API group
	v1 := app.Group("/api/v1")

	// Events sub-group
	events := v1.Group("/events")
	events.Post("/failure", IngestFailureEventHandler)

	// 404 catch-all — must be registered last.
	app.Use(func(c *fiber.Ctx) error {
		return c.Status(fiber.StatusNotFound).JSON(ErrorResponse{
			Status:  "error",
			Error:   "NOT_FOUND",
			Details: "The requested endpoint does not exist on this server",
		})
	})

	// ── 7. Graceful Shutdown ───────────────────────────────────────────────────
	// The server starts in a goroutine so this goroutine can park on the signal
	// channel. On SIGINT (Ctrl-C) or SIGTERM (container orchestrator), we:
	//   a) call app.ShutdownWithTimeout — drains in-flight requests (max 10s)
	//   b) close the database connection pool — prevents Neon connection leaks

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		slog.Info("Server listening",
			"addr", "0.0.0.0:"+port,
			"environment", appEnv,
		)
		if listenErr := app.Listen(":" + port); listenErr != nil {
			slog.Error("Fiber listen error — triggering shutdown",
				"error", listenErr.Error(),
			)
			// Signal main goroutine to begin shutdown sequence.
			quit <- syscall.SIGTERM
		}
	}()

	// Block until OS signal arrives.
	sig := <-quit
	slog.Info("Shutdown signal received — beginning graceful shutdown",
		"signal", sig.String(),
	)

	// Give in-flight requests up to 10 seconds to complete before force-closing.
	slog.Info("Draining in-flight requests (max 10s)...")
	if shutdownErr := app.ShutdownWithTimeout(10 * time.Second); shutdownErr != nil {
		slog.Error("Fiber shutdown timed out — some requests may have been dropped",
			"error", shutdownErr.Error(),
		)
	} else {
		slog.Info("Fiber server shut down cleanly")
	}

	// Close DB pool so Neon can reclaim the serverless compute unit.
	slog.Info("Closing database connection pool...")
	CloseDatabase()

	slog.Info("Graceful shutdown complete — goodbye",
		"service", "go-ingestion-api",
	)
}
