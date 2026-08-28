import {
  useCallback,
  useMemo,
  useRef,
} from "react";
import * as v from "valibot";
import {
  canonicalModelLabelOrNull,
  useSchemaFieldMetadata,
  type DataResourceMetadata,
} from "@angee/metadata";

import {
  usePreferenceSlice,
  type RuntimeUserPreferences,
} from "../../runtime";
import {
  resourceViewFavoritesFromJson,
  resourceViewFavoritesFromUnknown,
  type ResourceViewFavorite,
  type ResourceViewState,
} from "./resource-view-model";

export const RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY =
  "resource-view.favorites";
export const RESOURCE_VIEW_FAVORITES_VERSION = 1;

const EMPTY_FAVORITES: readonly ResourceViewFavorite[] = [];

interface ResourceViewFavoritesPreferences {
  version: typeof RESOURCE_VIEW_FAVORITES_VERSION;
  models: Readonly<Record<string, readonly ResourceViewFavorite[]>>;
}

interface ResourceViewFavoritesSlice {
  document: ResourceViewFavoritesPreferences;
  writable: boolean;
}

interface LegacyFavoritesImport {
  apply: (slice: ResourceViewFavoritesSlice) => ResourceViewFavoritesSlice;
  complete: () => void;
}

const EMPTY_FAVORITES_DOCUMENT: ResourceViewFavoritesPreferences = {
  version: RESOURCE_VIEW_FAVORITES_VERSION,
  models: {},
};
const EMPTY_FAVORITES_SLICE: ResourceViewFavoritesSlice = {
  document: EMPTY_FAVORITES_DOCUMENT,
  writable: true,
};
const LOCKED_FAVORITES_SLICE: ResourceViewFavoritesSlice = {
  document: EMPTY_FAVORITES_DOCUMENT,
  writable: false,
};

const FavoritesVersionEnvelopeSchema = v.object({
  version: v.optional(v.unknown()),
});

const ResourceViewFavoritesPreferencesSchema = v.object({
  version: v.literal(RESOURCE_VIEW_FAVORITES_VERSION),
  models: v.record(v.string(), v.unknown()),
});

export interface ResourceViewFavoritesState {
  savedFavorites: readonly ResourceViewFavorite[];
  saveFavorite?: (label: string) => void;
}

/** Server-backed resource-view favorites over the runtime preference contract. */
export function useResourceViewFavorites(
  modelSpelling: string | undefined,
  state: ResourceViewState,
): ResourceViewFavoritesState {
  const metadata = useSchemaFieldMetadata();
  const canonicalModel = useMemo(
    () => modelSpelling
      ? canonicalModelLabelOrNull(
          metadata.resources ?? [],
          modelSpelling,
          "resource-view favorites",
        )
      : null,
    [metadata, modelSpelling],
  );
  const {
    available,
    value: favoritesSlice,
    update: updateFavorites,
  } = usePreferenceSlice(
    RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY,
    readResourceViewFavoritesSlice,
    writeResourceViewFavoritesSlice,
  );
  const legacyImport = useRef<LegacyFavoritesImport | null>(null);
  const savedFavorites = canonicalModel
    ? favoritesSlice.document.models[canonicalModel] ?? EMPTY_FAVORITES
    : EMPTY_FAVORITES;
  const writable = available && favoritesSlice.writable && canonicalModel !== null;

  const saveFavorite = useCallback(
    (label: string) => {
      const trimmed = label.trim();
      if (!writable || !canonicalModel || !trimmed) return;
      const favorite = state.toFavorite(trimmed, savedFavorites);
      const migration = legacyImport.current ??= createLegacyFavoritesImport(
        metadata.resources ?? [],
      );
      void updateFavorites((current) =>
        appendResourceViewFavorite(
          migration.apply(current),
          canonicalModel,
          favorite,
        )
      ).then(
        () => migration.complete(),
        () => undefined,
      );
    },
    [
      canonicalModel,
      metadata.resources,
      savedFavorites,
      state,
      updateFavorites,
      writable,
    ],
  );

  if (!writable) return { savedFavorites: EMPTY_FAVORITES };
  return { savedFavorites, saveFavorite };
}

function appendResourceViewFavorite(
  current: ResourceViewFavoritesSlice,
  modelLabel: string,
  favorite: ResourceViewFavorite,
): ResourceViewFavoritesSlice {
  if (!current.writable) return current;
  const models = { ...current.document.models };
  models[modelLabel] = mergeFavorites(models[modelLabel], [favorite]);
  return {
    writable: true,
    document: {
      version: RESOURCE_VIEW_FAVORITES_VERSION,
      models,
    },
  };
}

/**
 * The stored favorites document, discriminated by version: an unknown version
 * reads empty and write-unavailable (never overwrite a future document); a
 * version-matched document reads writable with malformed favorites dropped
 * per model.
 */
export function readResourceViewFavoritesSlice(
  preferences: RuntimeUserPreferences,
): ResourceViewFavoritesSlice {
  const raw = preferences[RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY];
  if (raw === undefined) return EMPTY_FAVORITES_SLICE;
  const envelope = v.safeParse(FavoritesVersionEnvelopeSchema, raw);
  if (
    envelope.success
    && envelope.output.version !== undefined
    && envelope.output.version !== RESOURCE_VIEW_FAVORITES_VERSION
  ) {
    return LOCKED_FAVORITES_SLICE;
  }
  const document = v.safeParse(ResourceViewFavoritesPreferencesSchema, raw);
  if (!document.success) return EMPTY_FAVORITES_SLICE;
  // A version-matched document never loses siblings to one bad entry: each
  // model's list drops only its malformed favorites (the same recovery the
  // legacy import uses), so the next save cannot wipe healthy models.
  const models: Record<string, readonly ResourceViewFavorite[]> = {};
  for (const [model, favorites] of Object.entries(document.output.models)) {
    const kept = resourceViewFavoritesFromUnknown(favorites);
    if (kept.length > 0) models[model] = kept;
  }
  return {
    writable: true,
    document: { version: RESOURCE_VIEW_FAVORITES_VERSION, models },
  };
}

function writeResourceViewFavoritesSlice(
  preferences: RuntimeUserPreferences,
  slice: ResourceViewFavoritesSlice,
): RuntimeUserPreferences {
  if (!slice.writable) return preferences;
  return {
    ...preferences,
    [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: slice.document,
  };
}

// Legacy* removal marker: delete this migration block after one release with
// server-backed favorites; successful import removes every legacy storage key.
const LEGACY_FAVORITES_PREFIX = "angee:resource-view:";
const LEGACY_FAVORITES_SUFFIX = ":favorites";

function createLegacyFavoritesImport(
  resources: readonly DataResourceMetadata[],
): LegacyFavoritesImport {
  const { keys, models } = readLegacyFavorites(resources);
  let completed = false;
  return {
    apply(slice) {
      if (completed || !slice.writable) return slice;
      return {
        writable: true,
        document: {
          version: RESOURCE_VIEW_FAVORITES_VERSION,
          models: mergeFavoriteModels(slice.document.models, models),
        },
      };
    },
    complete() {
      if (completed) return;
      removeLegacyFavorites(keys);
      completed = true;
    },
  };
}

function readLegacyFavorites(
  resources: readonly DataResourceMetadata[],
): {
  keys: readonly string[];
  models: Readonly<Record<string, readonly ResourceViewFavorite[]>>;
} {
  const storage = favoriteStorage();
  if (!storage) return { keys: [], models: {} };
  const keys: string[] = [];
  const models: Record<string, readonly ResourceViewFavorite[]> = {};
  try {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (
        !key?.startsWith(LEGACY_FAVORITES_PREFIX)
        || !key.endsWith(LEGACY_FAVORITES_SUFFIX)
      ) {
        continue;
      }
      const spelling = key.slice(
        LEGACY_FAVORITES_PREFIX.length,
        -LEGACY_FAVORITES_SUFFIX.length,
      );
      const modelLabel = canonicalModelLabelOrNull(
        resources,
        spelling,
        "legacy resource-view favorites import",
      );
      if (!modelLabel) continue;
      const favorites = resourceViewFavoritesFromJson(storage.getItem(key));
      models[modelLabel] = mergeFavorites(models[modelLabel], favorites);
      keys.push(key);
    }
  } catch {
    return { keys: [], models: {} };
  }
  return { keys, models };
}

function mergeFavoriteModels(
  stored: Readonly<Record<string, readonly ResourceViewFavorite[]>>,
  imported: Readonly<Record<string, readonly ResourceViewFavorite[]>>,
): Record<string, readonly ResourceViewFavorite[]> {
  const models: Record<string, readonly ResourceViewFavorite[]> = { ...stored };
  for (const [modelLabel, favorites] of Object.entries(imported)) {
    models[modelLabel] = mergeFavorites(models[modelLabel], favorites);
  }
  return models;
}

function mergeFavorites(
  left: readonly ResourceViewFavorite[] | undefined,
  right: readonly ResourceViewFavorite[],
): readonly ResourceViewFavorite[] {
  const merged = new Map<string, ResourceViewFavorite>();
  for (const favorite of [...(left ?? []), ...right]) {
    if (!merged.has(favorite.id)) merged.set(favorite.id, favorite);
  }
  return [...merged.values()];
}

function removeLegacyFavorites(keys: readonly string[]): void {
  const storage = favoriteStorage();
  if (!storage) return;
  try {
    for (const key of keys) storage.removeItem(key);
  } catch {
    // A successful server write remains authoritative when storage is blocked.
  }
}

function favoriteStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}
