/**
 * Dashboard smoke test — verifies the main dashboard component mounts
 * and renders the primary heading without crashing.
 *
 * Run: npm test
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import RevenueRecoveryDashboard from "./RevenueRecoveryDashboard";

describe("RevenueRecoveryDashboard", () => {
  it("renders the primary heading without crashing", () => {
    render(<RevenueRecoveryDashboard />);
    // The h1 should contain the dashboard title
    expect(
      screen.getByRole("heading", { name: /Recovery Intelligence Dashboard/i })
    ).toBeInTheDocument();
  });

  it("renders the KPI section with at least one KPI label", () => {
    render(<RevenueRecoveryDashboard />);
    // 'Total Revenue Recovered' is one of the KPI card labels
    expect(screen.getByText(/Total Revenue Recovered/i)).toBeInTheDocument();
  });

  it("renders the Start Simulation button", () => {
    render(<RevenueRecoveryDashboard />);
    expect(
      screen.getByRole("button", { name: /Stream Simulated Events/i })
    ).toBeInTheDocument();
  });
});
