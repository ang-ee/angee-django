# Projects web

Task links use `useRouteHref().record(TASK_MODEL, id)`. This lets the shared task
surfaces stay inside an app's declared task collection/record routes, with the
canonical Projects route as fallback. Consumer apps declare their projection
through [the app manifest](../../../../packages/app/README.md); they do not copy
task surfaces or replace their navigation handlers.
