# Lessons Learned

## Recent
<!-- 10 most recent lessons, newest first -->

- Use dataclasses (not Pydantic) for Temporal workflow/activity data types to avoid needing pydantic_data_converter setup — simpler serialization out of the box (2026-04-03)
- For Temporal demo projects, keep calculate_feedback as a pure function inside the workflow rather than an activity — it's deterministic and doesn't need activity overhead (2026-04-03)
- When planning TDD steps for Temporal projects, mock activities using `@activity.defn(name="original_name")` in workflow tests to avoid needing a running Temporal server (2026-04-03)

## Categories
<!-- Lessons organized by topic -->

### Temporal
- Use dataclasses (not Pydantic) for Temporal workflow/activity data types to avoid needing pydantic_data_converter setup — simpler serialization out of the box (2026-04-03)
- For Temporal demo projects, keep calculate_feedback as a pure function inside the workflow rather than an activity — it's deterministic and doesn't need activity overhead (2026-04-03)
- When planning TDD steps for Temporal projects, mock activities using `@activity.defn(name="original_name")` in workflow tests to avoid needing a running Temporal server (2026-04-03)

### Architecture
- For teaching/demo projects, prioritize code clarity and minimal dependencies over production patterns — no factories, registries, or complex abstractions (2026-04-03)
