# Changelog

## 0.2.0

- Renamed to `jmag-agent-suite` (console script `jmag-mcp` and package `jmag_bridge` unchanged).
- Claude Code plugin and marketplace packaging: `/plugin marketplace add Zwl20085/jmag-agent-suite`,
  then `/plugin install jmag@jmag-agent-suite`.
- Health checks: `ZERO_CURRENT` is a warning in pre- and postflight (error only if flux linkage is also
  zero); postflight honours the known-message classifier levels; limits exposed as `health.THRESHOLDS`.
- CI runs the worker protocol tests on Python 3.8.
- (tools added by this release are listed in the README)

## 0.1.0

- Initial MCP server and Claude skills for JMAG-Designer with engineering feedback (setup lint,
  solver monitoring, physics invariants).
- Custom B-H material creation (`jmag_create_material`), connect/model image fixes.
- DC-dominant result tables in `jmag_get_result`; cleaner contour images.
