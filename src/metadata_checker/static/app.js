window.PROVIDERS_LIST = [];
window.SELECTED_PROVIDER_ID = null;
window.SELECTED_PROVIDER_NAME = null;
window.SELECTED_COUNTRY_CODE = null;
window.SELECTED_CHANNEL_KEY = null;
window.SELECTED_PROVIDER_TYPE = "";
window.PROVIDER_S3_CONFIG = {};
window.DYNAMIC_CHANNEL_OPTIONS = {};
window.CHANNEL_FETCH_INFLIGHT = {};
window.GROUPING_AUTH_CACHE = {
  refreshToken: null,
  accessToken: null,
  expiresAt: 0,
};

const COUNTRIES = [
  { code: "FI", name: "\uD83C\uDDEB\uD83C\uDDEE Finland" },
  { code: "NO", name: "\uD83C\uDDF3\uD83C\uDDF4 Norway" },
  { code: "SE", name: "\uD83C\uDDF8\uD83C\uDDEA Sweden" },
];

const MANUAL_UPLOAD_TARGET_BYTES_PER_REQUEST = 60 * 1024 * 1024;
const MANUAL_UPLOAD_MAX_FILES_PER_CHUNK = 1200;
const MANUAL_UPLOAD_PARALLEL_REQUESTS = 3;
const MAX_MANUAL_UPLOAD_SECTIONS = 10;
const GROUPING_REQUEST_TIMEOUT_MS = 10 * 60 * 1000;
const GROUPING_TOKEN_EXCHANGE_TIMEOUT_MS = 45000;
const GROUPING_PARALLEL_REQUESTS = MANUAL_UPLOAD_PARALLEL_REQUESTS;
const GROUPING_ACCESS_TOKEN_CACHE_MS = 170000;

function loadGroupingAuthCache() {
  try {
    const raw = window.sessionStorage.getItem("groupingAuthCache");
    if (!raw) {
      return;
    }
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return;
    }
    window.GROUPING_AUTH_CACHE = {
      refreshToken: parsed.refreshToken || null,
      accessToken: parsed.accessToken || null,
      expiresAt: Number(parsed.expiresAt || 0),
    };
  } catch {
    window.GROUPING_AUTH_CACHE = {
      refreshToken: null,
      accessToken: null,
      expiresAt: 0,
    };
  }
}

function saveGroupingAuthCache(cache) {
  window.GROUPING_AUTH_CACHE = cache;
  try {
    window.sessionStorage.setItem("groupingAuthCache", JSON.stringify(cache));
  } catch {
    // Ignore storage failures; in-memory cache still helps.
  }
}

function getCurrentRefreshToken() {
  const topToken = (document.getElementById("igRefreshToken")?.value || "").trim();
  if (topToken) {
    return topToken;
  }
  const groupingToken = (document.getElementById("groupingToken")?.value || "").trim();
  if (groupingToken) {
    return groupingToken;
  }
  const cache = window.GROUPING_AUTH_CACHE || {};
  return String(cache.refreshToken || "").trim();
}

function buildGroupingChunkRanges(files) {
  const ranges = [];
  const totalFiles = files.length;
  let start = 0;

  while (start < totalFiles) {
    let end = start;
    let bytes = 0;

    while (end < totalFiles && end - start < MANUAL_UPLOAD_MAX_FILES_PER_CHUNK) {
      const size = (files[end] && files[end].size) || 0;
      if (end > start && bytes + size > MANUAL_UPLOAD_TARGET_BYTES_PER_REQUEST) {
        break;
      }
      bytes += size;
      end += 1;

      if (bytes >= MANUAL_UPLOAD_TARGET_BYTES_PER_REQUEST) {
        break;
      }
    }

    if (end === start) {
      end = start + 1;
    }

    ranges.push({ start, end });
    start = end;
  }

  return ranges;
}

document.addEventListener("DOMContentLoaded", function () {
  setupAppMainTabs();
  loadGroupingAuthCache();
  setupForms();
  setupCountryDropdown();
  setupProviderDropdown();
  setupProviderTypeFilter();
  setupChannelDropdown();
  setupGroupingTokenChannelRefresh();
  loadProvidersList();
  loadProviderS3Config();
  initializeS3DateDefaults();
  setupGroupingChecker();
  setupIngestionTests();
});

function setupAppMainTabs() {
  const tabNavButtons = document.querySelectorAll(".app-main-tab-btn");
  tabNavButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetPaneId = btn.dataset.tab;
      switchAppMainTab(targetPaneId);
    });
  });
}

function switchAppMainTab(targetPaneId) {
  const tabNavButtons = document.querySelectorAll(".app-main-tab-btn");
  const tabPanes = document.querySelectorAll(".app-tab-pane");

  tabNavButtons.forEach((btn) => {
    if (btn.dataset.tab === targetPaneId) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });

  tabPanes.forEach((pane) => {
    if (pane.id === targetPaneId) {
      pane.classList.remove("hidden");
    } else {
      pane.classList.add("hidden");
    }
  });

  // Re-render Chart.js charts if switching to Analytics tab
  if (targetPaneId === "analyticsTabPane" && window.LAST_DATA_RESULTS) {
    renderChartJsCharts(window.LAST_DATA_RESULTS);
  }
}

function loadProviderS3Config() {
  fetch(`/static/providers_s3_config.json?v=${Date.now()}`, { cache: "no-store" })
    .then((response) => response.json())
    .then((data) => {
      window.PROVIDER_S3_CONFIG = data;
      console.log("✓ Provider S3 config loaded:", Object.keys(data).length, "providers");
    })
    .catch((err) => console.error("Failed to load provider S3 config:", err));
}

function initializeS3DateDefaults() {
  const today = new Date();
  const sevenDaysAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);

  const startDateInput = document.getElementById("dateRangeStart");
  const endDateInput = document.getElementById("dateRangeEnd");

  if (startDateInput) {
    startDateInput.valueAsDate = sevenDaysAgo;
  }
  if (endDateInput) {
    endDateInput.valueAsDate = today;
  }
}

function loadProvidersList() {
  fetch(`/static/providers_list.json?v=${Date.now()}`, { cache: "no-store" })
    .then((response) => response.json())
    .then((data) => {
      window.PROVIDERS_LIST = data;
      renderProviderDropdown(data, window.SELECTED_COUNTRY_CODE);
      console.log("✓ Providers loaded:", data.length, "providers");
    })
    .catch((err) => console.error("Failed to load providers:", err));
}

function selectProvider(providerId, providerName) {
  window.SELECTED_PROVIDER_ID = providerId;
  window.SELECTED_PROVIDER_NAME = providerName;
  window.SELECTED_CHANNEL_KEY = null;
  localStorage.setItem("selectedProviderId", providerId);

  refreshChannelDropdownForSelection(providerId);
  // Auto-populate S3 fields
  populateS3FieldsFromProvider(providerId);
}

function applyS3Config(config) {
  const s3BucketField = document.getElementById("s3Bucket");
  const s3OriginalPrefixField = document.getElementById("s3OriginalPrefix");
  const s3ConvertedPrefixField = document.getElementById("s3ConvertedPrefix");

  if (s3BucketField) {
    s3BucketField.value = (config && config.bucket) || "";
  }
  if (s3OriginalPrefixField) {
    s3OriginalPrefixField.value = (config && config.original_prefix) || "";
  }
  if (s3ConvertedPrefixField) {
    s3ConvertedPrefixField.value = (config && config.converted_prefix) || "";
  }
}

function getChannelOptionsForProvider(providerId) {
  const providerConfig = window.PROVIDER_S3_CONFIG[providerId];
  if (!providerConfig || typeof providerConfig !== "object") {
    return [];
  }

  const map = providerConfig.by_channel || providerConfig.channels || providerConfig.channel_prefixes;
  if (!map || typeof map !== "object" || Array.isArray(map)) {
    return [];
  }

  return Object.entries(map)
    .filter(([_, value]) => value && typeof value === "object")
    .map(([key, value]) => ({
      key,
      label: String(value.label || key),
      bucket: value.bucket || providerConfig.bucket || "",
      original_prefix: value.original_prefix || providerConfig.original_prefix || "",
      converted_prefix: value.converted_prefix || providerConfig.converted_prefix || "",
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function getChannelCacheKey(providerId, countryCode) {
  if (!providerId || !countryCode) {
    return "";
  }
  return `${String(countryCode).toUpperCase()}|${String(providerId).toLowerCase()}`;
}

function providerFamilyBase(providerId) {
  return String(providerId || "")
    .toLowerCase()
    .replace(/-(epg|vod|svod|event|deeplink|linear)$/, "");
}

function getProviderAliasHints(providerId) {
  const base = providerFamilyBase(providerId);
  const providers = Array.isArray(window.PROVIDERS_LIST) ? window.PROVIDERS_LIST : [];
  const aliases = new Set();

  for (const item of providers) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const itemId = String(item.providerId || "").trim();
    if (!itemId) {
      continue;
    }
    if (providerFamilyBase(itemId) !== base) {
      continue;
    }
    aliases.add(itemId);
    const itemName = String(item.providerName || "").trim();
    if (itemName) {
      aliases.add(itemName);
    }
  }

  aliases.add(String(providerId || "").trim());
  const selectedName = String(window.SELECTED_PROVIDER_NAME || "").trim();
  if (selectedName) {
    aliases.add(selectedName);
  }

  return Array.from(aliases).filter(Boolean).slice(0, 20);
}

function getEffectiveChannelOptions(providerId) {
  const cacheKey = getChannelCacheKey(providerId, window.SELECTED_COUNTRY_CODE);
  const liveOptions = window.DYNAMIC_CHANNEL_OPTIONS[cacheKey];
  const staticOptions = getChannelOptionsForProvider(providerId);

  if (Array.isArray(liveOptions) && liveOptions.length > 0) {
    const merged = [];
    const seenKeys = new Set();
    const seenLabels = new Set();

    for (const item of liveOptions) {
      merged.push(item);
      seenKeys.add(String(item.key || ""));
      seenLabels.add(String(item.label || "").toLowerCase());
    }

    for (const item of staticOptions) {
      const key = String(item.key || "");
      const labelLower = String(item.label || "").toLowerCase();
      if (seenKeys.has(key) || seenLabels.has(labelLower)) {
        continue;
      }
      merged.push(item);
      seenKeys.add(key);
      seenLabels.add(labelLower);
    }

    return merged.sort((a, b) => a.label.localeCompare(b.label));
  }

  if (staticOptions.length > 0) {
    return staticOptions;
  }

  return [];
}

function slugifyChannelKey(value) {
  const text = String(value || "").toLowerCase();
  const slug = text.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return slug || "channel";
}

function buildDefaultPrefixedPath(basePrefix, channelKey) {
  const base = String(basePrefix || "").trim();
  if (!base) {
    return "";
  }
  const withoutTrailing = base.endsWith("/") ? base.slice(0, -1) : base;
  return `${withoutTrailing}/${channelKey}/`;
}

function mapLiveChannelsToOptions(providerId, channelNames) {
  const providerConfig = window.PROVIDER_S3_CONFIG[providerId] || {};
  const staticOptions = getChannelOptionsForProvider(providerId);
  const staticByLabel = new Map(
    staticOptions.map((item) => [String(item.label || "").toLowerCase(), item])
  );
  const staticByKey = new Map(staticOptions.map((item) => [item.key, item]));

  const seenKeys = new Set();
  const mapped = [];

  for (const rawName of channelNames || []) {
    const label = String(rawName || "").trim();
    if (!label) {
      continue;
    }

    let key = slugifyChannelKey(label);
    if (seenKeys.has(key)) {
      let suffix = 2;
      while (seenKeys.has(`${key}-${suffix}`)) {
        suffix += 1;
      }
      key = `${key}-${suffix}`;
    }
    seenKeys.add(key);

    const labelLookup = staticByLabel.get(label.toLowerCase());
    const keyLookup = staticByKey.get(key);
    const override = labelLookup || keyLookup || null;

    mapped.push({
      key,
      label,
      bucket: (override && override.bucket) || providerConfig.bucket || "",
      original_prefix:
        (override && override.original_prefix) ||
        buildDefaultPrefixedPath(providerConfig.original_prefix, key),
      converted_prefix:
        (override && override.converted_prefix) ||
        buildDefaultPrefixedPath(providerConfig.converted_prefix, key),
    });
  }

  return mapped.sort((a, b) => a.label.localeCompare(b.label));
}

async function fetchLiveChannelOptions(providerId, countryCode) {
  const cacheKey = getChannelCacheKey(providerId, countryCode);
  if (!cacheKey) {
    return [];
  }

  const cached = window.DYNAMIC_CHANNEL_OPTIONS[cacheKey];
  if (Array.isArray(cached) && cached.length > 0) {
    return cached;
  }

  if (window.CHANNEL_FETCH_INFLIGHT[cacheKey]) {
    return window.CHANNEL_FETCH_INFLIGHT[cacheKey];
  }

  const promise = (async () => {
    const params = new URLSearchParams({
      provider_id: providerId,
      provider_name: String(window.SELECTED_PROVIDER_NAME || ""),
      provider_aliases: getProviderAliasHints(providerId).join("|"),
      country: String(countryCode || "").toUpperCase(),
    });

    const headers = {};
    const authCache = window.GROUPING_AUTH_CACHE || {};
    if (authCache.accessToken && Date.now() < Number(authCache.expiresAt || 0)) {
      headers["X-Access-Token"] = authCache.accessToken;
    } else {
      const typedRefreshToken = getCurrentRefreshToken();
      if (typedRefreshToken) {
        headers["X-Refresh-Token"] = typedRefreshToken;
      }
    }

    const response = await fetch(`/api/provider-channels?${params.toString()}`, {
      headers,
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(data.error || "Failed to load channels from pilot");
    }

    const names = Array.isArray(data.channels)
      ? data.channels.map((item) => String(item || "").trim()).filter(Boolean)
      : [];

    const options = mapLiveChannelsToOptions(providerId, names);
    window.DYNAMIC_CHANNEL_OPTIONS[cacheKey] = options;

    return options;
  })();

  window.CHANNEL_FETCH_INFLIGHT[cacheKey] = promise;
  try {
    return await promise;
  } finally {
    delete window.CHANNEL_FETCH_INFLIGHT[cacheKey];
  }
}

function setupGroupingTokenChannelRefresh() {
  const tokenInputs = [
    document.getElementById("igRefreshToken"),
    document.getElementById("groupingToken"),
  ].filter(Boolean);

  if (tokenInputs.length === 0) {
    return;
  }

  const cachedToken = String((window.GROUPING_AUTH_CACHE || {}).refreshToken || "").trim();
  if (cachedToken) {
    for (const input of tokenInputs) {
      if (input && !String(input.value || "").trim()) {
        input.value = cachedToken;
      }
    }
  }

  const maybeRefreshChannels = (event) => {
    const token = getCurrentRefreshToken();
    if (!token) {
      return;
    }

    const sourceInput = event && event.target ? event.target : null;
    if (sourceInput) {
      for (const input of tokenInputs) {
        if (input && input !== sourceInput) {
          input.value = token;
        }
      }
    }

    const cache = window.GROUPING_AUTH_CACHE || {};
    if (cache.refreshToken !== token) {
      saveGroupingAuthCache({
        refreshToken: token,
        accessToken: null,
        expiresAt: 0,
      });
    }

    const providerId = window.SELECTED_PROVIDER_ID;
    const countryCode = window.SELECTED_COUNTRY_CODE;
    if (!providerId || !countryCode) {
      return;
    }

    const cacheKey = getChannelCacheKey(providerId, countryCode);
    if (cacheKey) {
      delete window.DYNAMIC_CHANNEL_OPTIONS[cacheKey];
    }

    refreshChannelDropdownForSelection(providerId);
  };

  for (const tokenInput of tokenInputs) {
    tokenInput.addEventListener("change", maybeRefreshChannels);
    tokenInput.addEventListener("blur", maybeRefreshChannels);
  }
}

async function refreshChannelDropdownForSelection(providerId) {
  const countryCode = window.SELECTED_COUNTRY_CODE;
  if (!providerId || !countryCode) {
    resetChannelDropdown();
    return;
  }

  // Render immediately from static/cached options for snappy UX,
  // then refresh with live pilot data in the background.
  const immediateOptions = getEffectiveChannelOptions(providerId);
  if (immediateOptions.length > 0) {
    renderChannelDropdown(providerId, immediateOptions);
  }

  // Keep the selector fully hidden unless we have actual channel options.
  if (immediateOptions.length === 0) {
    resetChannelDropdown();
  }

  const selectedProviderAtStart = window.SELECTED_PROVIDER_ID;
  const selectedCountryAtStart = window.SELECTED_COUNTRY_CODE;

  try {
    const options = await fetchLiveChannelOptions(providerId, countryCode);

    if (
      selectedProviderAtStart !== window.SELECTED_PROVIDER_ID ||
      selectedCountryAtStart !== window.SELECTED_COUNTRY_CODE
    ) {
      return;
    }

    if (options.length > 0) {
      renderChannelDropdown(providerId, options);
      return;
    }
    if (immediateOptions.length === 0) {
      resetChannelDropdown();
    }
  } catch (error) {
    console.warn("Failed to load pilot channels:", error);
    const message = String(error && error.message ? error.message : "").toLowerCase();
    if (message.includes("refresh/access token is required")) {
      if (immediateOptions.length === 0) {
        resetChannelDropdown();
      }
      return;
    }
    if (immediateOptions.length === 0) {
      resetChannelDropdown();
    }
  }
}

function populateS3FieldsFromProvider(providerId, channelKey = null) {
  const providerConfig = window.PROVIDER_S3_CONFIG[providerId];

  if (!providerConfig) {
    console.warn("No S3 config found for provider:", providerId);
    applyS3Config(null);
    return;
  }

  const channelOptions = getEffectiveChannelOptions(providerId);
  const selectedChannel = channelOptions.find((item) => item.key === channelKey) || null;

  applyS3Config(selectedChannel || providerConfig);
  console.log("✓ S3 config populated for provider:", providerId, selectedChannel ? `(channel=${selectedChannel.key})` : "");
}

function resetChannelDropdown() {
  const container = document.getElementById("channelDropdownContainer");
  const menu = document.getElementById("channelDropdown");
  const label = document.getElementById("channelLabel");
  const toggle = document.getElementById("channelToggle");

  window.SELECTED_CHANNEL_KEY = null;
  if (menu) {
    menu.innerHTML = "";
    menu.classList.remove("show");
  }
  if (label) {
    label.textContent = "Select a channel";
  }
  if (toggle) {
    toggle.setAttribute("aria-label", "Select a channel");
    toggle.setAttribute("aria-expanded", "false");
  }
  if (container) {
    container.classList.add("hidden");
  }
}

function renderChannelDropdown(providerId, optionsOverride = null) {
  const container = document.getElementById("channelDropdownContainer");
  const menu = document.getElementById("channelDropdown");
  const label = document.getElementById("channelLabel");
  const toggle = document.getElementById("channelToggle");

  if (!container || !menu || !label || !toggle) {
    return;
  }

  const options = Array.isArray(optionsOverride)
    ? optionsOverride
    : getEffectiveChannelOptions(providerId);
  if (options.length === 0) {
    resetChannelDropdown();
    return;
  }

  container.classList.remove("hidden");
  label.textContent = "Select a channel";
  toggle.setAttribute("aria-label", "Select a channel");
  toggle.setAttribute("aria-expanded", "false");

  menu.innerHTML = options
    .map((channel) => `
      <button type="button" class="dropdown-item" data-channel-key="${channel.key}" data-channel-label="${channel.label}" role="menuitem">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
          class="check-icon"
        >
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        <span>${channel.label}</span>
      </button>
    `)
    .join("");
}

function extractUniqueProviders(providers) {
  const unique = new Map();

  for (const provider of providers || []) {
    const providerId = provider.providerId || provider.adapterId;
    const providerName = provider.providerName || provider.name || providerId;

    if (!providerId || !providerName || unique.has(providerId)) {
      continue;
    }

    unique.set(providerId, {
      providerId,
      providerName,
    });
  }

  return Array.from(unique.values()).sort((a, b) =>
    a.providerName.localeCompare(b.providerName)
  );
}

function providerMatchesTypeFilter(provider, typeFilter) {
  if (!typeFilter || typeFilter === "all") {
    return true;
  }
  const types = Array.isArray(provider.types) ? provider.types : [];
  return types.map((t) => String(t).toLowerCase()).includes(typeFilter);
}

function renderProviderDropdown(providers, countryCode) {
  const menu = document.getElementById("myDropdown");
  const label = document.getElementById("providerLabel");
  const toggleButton = document.getElementById("providerToggle");
  const providerContainer = document.getElementById("providerDropdownContainer");

  if (!menu || !providerContainer) {
    return;
  }

  const typeFilter = (window.SELECTED_PROVIDER_TYPE || "").trim().toLowerCase();
  const hasTypeSelection = Boolean(typeFilter);
  const hasCountrySelection = Boolean(String(countryCode || "").trim());

  if (!hasTypeSelection || !hasCountrySelection) {
    providerContainer.classList.add("hidden");
    menu.innerHTML = '<div class="dropdown-empty">Select country and EPG/VOD/Event first</div>';
    if (label && toggleButton) {
      label.textContent = "Select a provider";
      toggleButton.setAttribute("aria-label", "Select country and provider type first");
    }
    window.SELECTED_PROVIDER_ID = null;
    window.SELECTED_PROVIDER_NAME = null;
    window.SELECTED_CHANNEL_KEY = null;
    resetChannelDropdown();
    localStorage.removeItem("selectedProviderId");
    return;
  }

  providerContainer.classList.remove("hidden");

  // Keep IG-style extraction (id/name normalization + dedupe), but apply it
  // to country-scoped data so each country gets its own adapter list.
  const countryScopedProviders = countryCode
    ? providers.filter(
        (provider) =>
          !Array.isArray(provider.countries) ||
          provider.countries.length === 0 ||
          provider.countries.includes(countryCode)
      )
    : providers;

  const typeScopedProviders = countryScopedProviders.filter((provider) =>
    providerMatchesTypeFilter(provider, typeFilter)
  );

  const uniqueProviders = extractUniqueProviders(typeScopedProviders);

  if (uniqueProviders.length === 0) {
    menu.innerHTML = `<div class="dropdown-empty">No providers found for this country</div>`;
  } else {
    menu.innerHTML = uniqueProviders
      .map((provider) => {
        return `
          <button type="button" class="dropdown-item" data-provider="${provider.providerName}" data-provider-id="${provider.providerId}" role="menuitem">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
              class="check-icon"
            >
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
            <span>${provider.providerName}</span>
          </button>
        `;
      })
      .join("");
  }

  if (label && toggleButton) {
    label.textContent = "Select a provider";
    toggleButton.setAttribute(
      "aria-label",
      "No provider selected. Select a provider",
    );
  }

  window.SELECTED_PROVIDER_ID = null;
  window.SELECTED_PROVIDER_NAME = null;
  resetChannelDropdown();
  localStorage.removeItem("selectedProviderId");

  window.parseJson = parseJson;
}

function parseJson() {
  const obj = window.PROVIDERS_LIST;

  if (!obj || obj.length === 0) {
    console.error("Providers not yet loaded. Try again in a moment.");
    return;
  }

  console.log("Type:", typeof obj);
  console.log("Total providers:", obj.length);
  console.log("Providers:", obj);
}

function setupForms() {
  const unifiedForm = document.getElementById("unifiedForm");
  const s3Form = document.getElementById("s3Form");
  const modeSelect = document.getElementById("modeSelect");

  if (unifiedForm) {
    unifiedForm.addEventListener("submit", handleUnifiedSubmit);
  }

  if (s3Form) {
    s3Form.addEventListener("submit", handleS3Submit);
  }

  if (modeSelect) {
    modeSelect.addEventListener("change", handleModeChange);
  }

  // Clear buttons
  const clearFileBtn = document.getElementById("clear");
  const clearS3Btn = document.getElementById("clearS3");

  if (clearFileBtn) {
    clearFileBtn.addEventListener("click", handleClearFileMode);
  }

  if (clearS3Btn) {
    clearS3Btn.addEventListener("click", handleClearS3Mode);
  }
}

function handleModeChange() {
  const modeSelect = document.getElementById("modeSelect");
  const fileForm = document.getElementById("unifiedForm");
  const s3Form = document.getElementById("s3Form");
  const mode = modeSelect.value;

  if (mode === "file") {
    fileForm.classList.remove("hidden");
    s3Form.classList.add("hidden");
  } else if (mode === "s3") {
    fileForm.classList.add("hidden");
    s3Form.classList.remove("hidden");
  }
}

function setupProviderDropdown() {
  const toggleButton = document.getElementById("providerToggle");
  const menu = document.getElementById("myDropdown");
  const label = document.getElementById("providerLabel");
  const dropdown = toggleButton ? toggleButton.closest(".dropdown") : null;

  if (!toggleButton || !menu || !label || !dropdown) {
    return;
  }

  function openMenu() {
    menu.classList.add("show");
    toggleButton.setAttribute("aria-expanded", "true");
  }

  function closeMenu() {
    menu.classList.remove("show");
    toggleButton.setAttribute("aria-expanded", "false");
  }

  toggleButton.addEventListener("click", function (event) {
    event.stopPropagation();
    if (menu.classList.contains("show")) {
      closeMenu();
    } else {
      openMenu();
    }
  });

  menu.addEventListener("click", function (event) {
    const item = event.target.closest(".dropdown-item");
    if (!item) {
      return;
    }

    menu
      .querySelectorAll(".dropdown-item")
      .forEach((el) => el.classList.remove("selected"));
    item.classList.add("selected");

    const providerName = item.dataset.provider || item.textContent.trim();
    const providerId = item.dataset.providerId;

    selectProvider(providerId, providerName);
    label.textContent = providerName;
    toggleButton.setAttribute(
      "aria-label",
      `Selected provider: ${providerName}`,
    );

    console.log("Selected provider:", { providerId, providerName });

    closeMenu();
  });

  window.addEventListener("click", function (event) {
    if (!event.target.closest(".dropdown")) {
      closeMenu();
    }
  });

  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeMenu();
    }
  });
}

function setupChannelDropdown() {
  const toggleButton = document.getElementById("channelToggle");
  const menu = document.getElementById("channelDropdown");
  const label = document.getElementById("channelLabel");
  const dropdown = toggleButton ? toggleButton.closest(".dropdown") : null;

  if (!toggleButton || !menu || !label || !dropdown) {
    return;
  }

  function openMenu() {
    menu.classList.add("show");
    toggleButton.setAttribute("aria-expanded", "true");
  }

  function closeMenu() {
    menu.classList.remove("show");
    toggleButton.setAttribute("aria-expanded", "false");
  }

  toggleButton.addEventListener("click", function (event) {
    event.stopPropagation();
    if (dropdown.classList.contains("hidden")) {
      return;
    }
    if (menu.classList.contains("show")) {
      closeMenu();
    } else {
      openMenu();
    }
  });

  menu.addEventListener("click", function (event) {
    const item = event.target.closest(".dropdown-item");
    if (!item) {
      return;
    }

    menu
      .querySelectorAll(".dropdown-item")
      .forEach((el) => el.classList.remove("selected"));
    item.classList.add("selected");

    const channelKey = item.dataset.channelKey;
    const channelLabel = item.dataset.channelLabel || item.textContent.trim();

    window.SELECTED_CHANNEL_KEY = channelKey;
    label.textContent = channelLabel;
    toggleButton.setAttribute("aria-label", `Selected channel: ${channelLabel}`);

    if (window.SELECTED_PROVIDER_ID) {
      populateS3FieldsFromProvider(window.SELECTED_PROVIDER_ID, channelKey);
    }

    closeMenu();
  });

  window.addEventListener("click", function (event) {
    if (!event.target.closest("#channelDropdownContainer")) {
      closeMenu();
    }
  });

  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeMenu();
    }
  });
}

function setupCountryDropdown() {
  const toggleButton = document.getElementById("countryToggle");
  const menu = document.getElementById("countryDropdown");
  const label = document.getElementById("countryLabel");
  const dropdown = toggleButton ? toggleButton.closest(".dropdown") : null;

  if (!toggleButton || !menu || !label || !dropdown) {
    return;
  }

  menu.innerHTML = COUNTRIES.map((country) => {
    return `
      <button type="button" class="dropdown-item" data-country-code="${country.code}" data-country-name="${country.name}" role="menuitem">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
          class="check-icon"
        >
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        <span>${country.name}</span>
      </button>
    `;
  }).join("");

  function openMenu() {
    // Close provider dropdown if open
    const providerMenu = document.getElementById("myDropdown");
    const providerToggle = document.getElementById("providerToggle");
    if (providerMenu) providerMenu.classList.remove("show");
    if (providerToggle) providerToggle.setAttribute("aria-expanded", "false");

    menu.classList.add("show");
    toggleButton.setAttribute("aria-expanded", "true");
  }

  function closeMenu() {
    menu.classList.remove("show");
    toggleButton.setAttribute("aria-expanded", "false");
  }

  toggleButton.addEventListener("click", function (event) {
    event.stopPropagation();
    if (menu.classList.contains("show")) {
      closeMenu();
    } else {
      openMenu();
    }
  });

  menu.addEventListener("click", function (event) {
    const item = event.target.closest(".dropdown-item");
    if (!item) return;

    menu.querySelectorAll(".dropdown-item").forEach((el) => el.classList.remove("selected"));
    item.classList.add("selected");

    const countryCode = item.dataset.countryCode;
    const countryName = item.dataset.countryName;

    const previousCountryCode = window.SELECTED_COUNTRY_CODE;

    window.SELECTED_COUNTRY_CODE = countryCode;
    label.textContent = countryName;
    toggleButton.setAttribute("aria-label", `Selected country: ${countryName}`);

    if (previousCountryCode && previousCountryCode !== countryCode) {
      // Drop dynamic cache from previous country so a new selection always
      // reflects live channels for the current country.
      window.DYNAMIC_CHANNEL_OPTIONS = {};
      window.CHANNEL_FETCH_INFLIGHT = {};
    }

    if (window.PROVIDERS_LIST && window.PROVIDERS_LIST.length > 0) {
      renderProviderDropdown(window.PROVIDERS_LIST, countryCode);
    }

    closeMenu();
  });

  window.addEventListener("click", function (event) {
    if (!event.target.closest("#countryDropdownContainer")) {
      closeMenu();
    }
  });

  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeMenu();
    }
  });
}

function setupProviderTypeFilter() {
  const group = document.getElementById("providerTypeFilter");
  if (!group) {
    return;
  }

  group.addEventListener("click", function (event) {
    const button = event.target.closest(".type-filter-btn");
    if (!button) {
      return;
    }

    const requestedType = String(button.dataset.providerType || "").trim().toLowerCase();
    const activeButton = group.querySelector(".type-filter-btn.active");
    const isSameAsActive = activeButton === button;

    group
      .querySelectorAll(".type-filter-btn")
      .forEach((btn) => btn.classList.remove("active"));

    if (isSameAsActive) {
      window.SELECTED_PROVIDER_TYPE = "";
    } else {
      button.classList.add("active");
      window.SELECTED_PROVIDER_TYPE = requestedType;
    }

    // Selecting a new type may invalidate the currently selected provider,
    // so reset provider/channel selections the same way a country switch does.
    if (window.PROVIDERS_LIST && window.PROVIDERS_LIST.length > 0) {
      renderProviderDropdown(window.PROVIDERS_LIST, window.SELECTED_COUNTRY_CODE);
    }
  });
}

function toggleDropdown() {}

async function parseApiResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  const rawText = await response.text();

  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(rawText);
    } catch {
      return { error: "Server returned invalid JSON." };
    }
  }

  const compact = rawText.replace(/\s+/g, " ").trim();
  return {
    error: `Server returned non-JSON response (${response.status}). ${compact.slice(0, 180)}`,
  };
}

function waitForNextPaint() {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(resolve);
    });
  });
}

function getSelectedUploadFiles(fileInputId, folderInputId) {
  const fileInput = document.getElementById(fileInputId);
  const folderInput = document.getElementById(folderInputId);
  return [
    ...(fileInput ? Array.from(fileInput.files || []) : []),
    ...(folderInput ? Array.from(folderInput.files || []) : []),
  ].filter(isUploadMetadataFile);
}

function isUploadMetadataFile(file) {
  const relativePath = String(file.webkitRelativePath || file.name || "");
  const parts = relativePath.split("/").filter(Boolean);
  const filename = parts[parts.length - 1] || "";

  if (!filename || filename.startsWith(".") || filename === "__MACOSX") {
    return false;
  }

  if (parts.some((part) => part.startsWith(".") || part === "__MACOSX")) {
    return false;
  }

  return /\.(json|xml)$/i.test(filename);
}

async function showLoadingInstantly(initialMessage = "Progress: 1%") {
  showLoading(true);
  setLoadingMessage(initialMessage);
  hideResults();
  await waitForNextPaint();
}

async function handleUnifiedSubmit(e) {
  e.preventDefault();

  await showLoadingInstantly("Progress: 1%");

  const originalFiles = getSelectedUploadFiles("originalFiles", "originalFolderFiles");
  const convertedFiles = getSelectedUploadFiles("convertedFiles", "convertedFolderFiles");
  const hasConverted = convertedFiles.length > 0;

  if (originalFiles.length === 0) {
    showLoading(false);
    showError("Please select at least one original metadata file");
    return;
  }

  if (!window.SELECTED_PROVIDER_ID) {
    showLoading(false);
    showError("Please select a provider before running checks");
    return;
  }

  if (hasConverted && convertedFiles.length !== originalFiles.length) {
    showLoading(false);
    showError(
      `Original and converted file counts must match for chunked paired comparison. Got original=${originalFiles.length}, converted=${convertedFiles.length}.`,
    );
    return;
  }

  const chunkRanges = buildUnifiedChunkRanges(originalFiles, convertedFiles, hasConverted);
  const totalChunks = chunkRanges.length;
  if (totalChunks === 0) {
    showLoading(false);
    showError("No valid files found to process");
    return;
  }

  const sectionCount = Math.min(MAX_MANUAL_UPLOAD_SECTIONS, totalChunks);
  const chunksPerSection = Math.ceil(totalChunks / sectionCount);

  let stopProgressTicker = () => {};

  try {
    const aggregated = {
      metadata_type: hasConverted ? "unified" : "original",
      provider_id: window.SELECTED_PROVIDER_ID || null,
      provider_config_path: null,
      provider_resolution: null,
      original_records_checked: 0,
      converted_records_checked: 0,
      summary: {
        total_findings: 0,
        by_check: {},
        by_severity: {},
      },
      findings: [],
      enabled_error_codes: [],
      analytics_matrix_fields: [],
      analytics_field_coverage: {
        fields: [],
        channels: [],
      },
    };

    const parallelism = Math.min(MANUAL_UPLOAD_PARALLEL_REQUESTS, totalChunks);
    let nextChunkIndex = 0;
    let completedChunks = 0;
    let displayedProgress = 0;
    let targetProgress = 0;
    let progressTimerId = null;

    const startProgressTicker = () => {
      if (progressTimerId !== null) {
        return;
      }

      progressTimerId = window.setInterval(() => {
        if (displayedProgress >= targetProgress) {
          return;
        }
        displayedProgress += 1;
        setLoadingMessage(`Progress: ${displayedProgress}%`);
      }, 15);
    };

    const setProgressTarget = (percent) => {
      const clamped = Math.max(1, Math.min(100, Math.round(percent)));
      targetProgress = Math.max(targetProgress, clamped);
      startProgressTicker();
    };

    const waitForDisplayedProgress = (target) => {
      return new Promise((resolve) => {
        setProgressTarget(target);
        const waiter = window.setInterval(() => {
          if (displayedProgress >= target) {
            window.clearInterval(waiter);
            resolve();
          }
        }, 10);
      });
    };

    stopProgressTicker = () => {
      if (progressTimerId !== null) {
        window.clearInterval(progressTimerId);
        progressTimerId = null;
      }
    };

    setProgressTarget(1);

    const worker = async () => {
      while (true) {
        const chunkIndex = nextChunkIndex;
        nextChunkIndex += 1;

        if (chunkIndex >= totalChunks) {
          return;
        }

        const range = chunkRanges[chunkIndex];
        const section = Math.min(sectionCount, Math.floor(chunkIndex / chunksPerSection) + 1);

        setProgressTarget(calculateProgressPercent(completedChunks, totalChunks));

        const chunkResults = await submitUnifiedRangeWithAdaptiveSplit({
          originalFiles,
          convertedFiles,
          hasConverted,
          providerId: window.SELECTED_PROVIDER_ID,
          start: range.start,
          end: range.end,
          section,
          sectionCount,
          chunkIndex,
          totalChunks,
          getProgressPercent: () => calculateProgressPercent(completedChunks, totalChunks),
          setProgressTarget,
        });

        for (const data of chunkResults) {
          mergeAggregatedResults(aggregated, data);
        }
        completedChunks += 1;
        setProgressTarget(calculateProgressPercent(completedChunks, totalChunks));
      }
    };

    await Promise.all(Array.from({ length: parallelism }, () => worker()));
    await waitForDisplayedProgress(100);
    stopProgressTicker();

    displayResults(aggregated);
  } catch (error) {
    showError(
      `Error: ${error.message}. Retry, and if needed use run_batch_upload.py for very large sets.`,
    );
  } finally {
    stopProgressTicker();
    showLoading(false);
  }
}

async function submitUnifiedRangeWithAdaptiveSplit(params) {
  const {
    originalFiles,
    convertedFiles,
    hasConverted,
    providerId,
    start,
    end,
    section,
    sectionCount,
    chunkIndex,
    totalChunks,
    getProgressPercent,
    setProgressTarget,
  } = params;

  const formData = new FormData();
  formData.append("provider_id", providerId);

  for (let i = start; i < end; i++) {
    formData.append("original_files", originalFiles[i]);
  }
  if (hasConverted) {
    for (let i = start; i < end; i++) {
      formData.append("converted_files", convertedFiles[i]);
    }
  }

  const response = await fetch("/api/check/unified", {
    method: "POST",
    body: formData,
  });
  const data = await parseApiResponse(response);

  if (response.ok) {
    return [data];
  }

  if (!isPayloadTooLarge(response.status, data) || end - start <= 1) {
    const context = `Section ${section}/${sectionCount} failed (chunk ${chunkIndex + 1}/${totalChunks}, files ${start + 1}-${end})`;
    if (end - start <= 1 && isPayloadTooLarge(response.status, data)) {
      throw new Error(
        `${context}: single file is too large for current server limit. File index ${start + 1}.`,
      );
    }
    throw new Error(`${context}: ${data.error || "An error occurred"}`);
  }

  // Adaptive fallback: split only the oversized chunk and continue.
  const mid = start + Math.floor((end - start) / 2);
  if (setProgressTarget) {
    setProgressTarget(getProgressPercent ? getProgressPercent() : 1);
  }

  const left = await submitUnifiedRangeWithAdaptiveSplit({
    originalFiles,
    convertedFiles,
    hasConverted,
    providerId,
    start,
    end: mid,
    section,
    sectionCount,
    chunkIndex,
    totalChunks,
    getProgressPercent,
    setProgressTarget,
  });
  const right = await submitUnifiedRangeWithAdaptiveSplit({
    originalFiles,
    convertedFiles,
    hasConverted,
    providerId,
    start: mid,
    end,
    section,
    sectionCount,
    chunkIndex,
    totalChunks,
    getProgressPercent,
    setProgressTarget,
  });

  return [...left, ...right];
}

function isPayloadTooLarge(status, data) {
  if (status === 413) {
    return true;
  }

  const msg = ((data && data.error) || "").toLowerCase();
  return msg.includes("payload too large") || msg.includes("maximum request size");
}

function calculateProgressPercent(completed, total) {
  if (!total || total <= 0) {
    return 0;
  }
  const clampedCompleted = Math.max(0, Math.min(completed, total));
  return Math.round((clampedCompleted / total) * 100);
}

function buildUnifiedChunkRanges(originalFiles, convertedFiles, hasConverted) {
  const ranges = [];
  const totalFiles = originalFiles.length;
  let start = 0;

  while (start < totalFiles) {
    let end = start;
    let bytes = 0;

    while (end < totalFiles && end - start < MANUAL_UPLOAD_MAX_FILES_PER_CHUNK) {
      const originalSize = (originalFiles[end] && originalFiles[end].size) || 0;
      const convertedSize = hasConverted
        ? ((convertedFiles[end] && convertedFiles[end].size) || 0)
        : 0;
      const pairBytes = originalSize + convertedSize;

      if (end > start && bytes + pairBytes > MANUAL_UPLOAD_TARGET_BYTES_PER_REQUEST) {
        break;
      }

      bytes += pairBytes;
      end += 1;

      if (bytes >= MANUAL_UPLOAD_TARGET_BYTES_PER_REQUEST) {
        break;
      }
    }

    if (end === start) {
      end = start + 1;
    }

    ranges.push({ start, end });
    start = end;
  }

  return ranges;
}

function mergeAggregatedResults(aggregated, data) {
  if (!aggregated.provider_id && data.provider_id) {
    aggregated.provider_id = data.provider_id;
  }
  if (!aggregated.provider_config_path && data.provider_config_path) {
    aggregated.provider_config_path = data.provider_config_path;
  }
  if (!aggregated.provider_resolution && data.provider_resolution) {
    aggregated.provider_resolution = data.provider_resolution;
  }

  aggregated.original_records_checked += data.original_records_checked || 0;
  aggregated.converted_records_checked += data.converted_records_checked || 0;

  const summary = data.summary || {};
  aggregated.summary.total_findings += summary.total_findings || 0;

  const byCheck = summary.by_check || {};
  for (const [checkId, count] of Object.entries(byCheck)) {
    aggregated.summary.by_check[checkId] =
      (aggregated.summary.by_check[checkId] || 0) + (count || 0);
  }

  const bySeverity = summary.by_severity || {};
  for (const [severity, count] of Object.entries(bySeverity)) {
    aggregated.summary.by_severity[severity] =
      (aggregated.summary.by_severity[severity] || 0) + (count || 0);
  }

  if (Array.isArray(data.findings) && data.findings.length > 0) {
    aggregated.findings.push(...data.findings);
  }

  if (Array.isArray(data.enabled_error_codes)) {
    aggregated.enabled_error_codes = Array.from(new Set([
      ...(aggregated.enabled_error_codes || []),
      ...data.enabled_error_codes,
    ]));
  }

  if (
    Array.isArray(data.analytics_matrix_fields)
    && data.analytics_matrix_fields.length > 0
    && (!aggregated.analytics_matrix_fields || aggregated.analytics_matrix_fields.length === 0)
  ) {
    aggregated.analytics_matrix_fields = data.analytics_matrix_fields;
  }

  const incomingCoverage = data.analytics_field_coverage;
  if (!incomingCoverage || !Array.isArray(incomingCoverage.channels)) {
    return;
  }

  if (
    Array.isArray(incomingCoverage.fields)
    && incomingCoverage.fields.length > 0
    && aggregated.analytics_field_coverage.fields.length === 0
  ) {
    aggregated.analytics_field_coverage.fields = incomingCoverage.fields;
  }

  const channelsByName = new Map(
    aggregated.analytics_field_coverage.channels.map((channel) => [channel.channel, channel]),
  );

  for (const incomingChannel of incomingCoverage.channels) {
    if (!incomingChannel || !incomingChannel.channel) {
      continue;
    }

    if (!channelsByName.has(incomingChannel.channel)) {
      channelsByName.set(incomingChannel.channel, {
        channel: incomingChannel.channel,
        records: 0,
        fields: {},
      });
    }

    const targetChannel = channelsByName.get(incomingChannel.channel);
    targetChannel.records += incomingChannel.records || 0;
    for (const [label, incomingStats] of Object.entries(incomingChannel.fields || {})) {
      const currentStats = targetChannel.fields[label] || { checked: 0, passed: 0, failed: 0 };
      targetChannel.fields[label] = {
        checked: currentStats.checked + (incomingStats.checked || 0),
        passed: currentStats.passed + (incomingStats.passed || 0),
        failed: currentStats.failed + (incomingStats.failed || 0),
      };
    }
  }

  aggregated.analytics_field_coverage.channels = Array.from(channelsByName.values())
    .sort((a, b) => a.channel.localeCompare(b.channel));
}

async function handleS3Submit(e) {
  e.preventDefault();

  await showLoadingInstantly("Progress: 1%");

  const bucket = document.getElementById("s3Bucket").value.trim();
  const originalPrefix = document.getElementById("s3OriginalPrefix").value.trim();
  const convertedPrefix = document.getElementById("s3ConvertedPrefix").value.trim();
  const startDate = document.getElementById("dateRangeStart").value;
  const endDate = document.getElementById("dateRangeEnd").value;

  if (!window.SELECTED_PROVIDER_ID) {
    showLoading(false);
    showError("Please select a provider before running checks");
    return;
  }

  if (!bucket) {
    showLoading(false);
    showError("Please enter the S3 bucket name");
    return;
  }

  if (!originalPrefix) {
    showLoading(false);
    showError("Please enter the original metadata prefix");
    return;
  }

  if (!startDate || !endDate) {
    showLoading(false);
    showError("Please select both start and end dates");
    return;
  }

  const startDateObj = new Date(startDate);
  const endDateObj = new Date(endDate);

  if (startDateObj > endDateObj) {
    showLoading(false);
    showError("Start date cannot be after end date");
    return;
  }

  const payload = {
    provider_id: window.SELECTED_PROVIDER_ID,
    bucket: bucket,
    original_prefix: originalPrefix,
    converted_prefix: convertedPrefix || "",
    start_date: startDate,
    end_date: endDate,
    aws_profile:
      (window.PROVIDER_S3_CONFIG[window.SELECTED_PROVIDER_ID] || {}).aws_profile || "",
  };

  try {
    const response = await fetch("/api/check/s3", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await parseApiResponse(response);

    if (!response.ok) {
      showError(data.error || "An error occurred");
      return;
    }

    displayResults(data);
  } catch (error) {
    showError(`Error: ${error.message}`);
  } finally {
    showLoading(false);
  }
}

const button = document.getElementById("clear");

async function handleClearFileMode() {
  const resultsDiv = document.getElementById("results");
  const originalInput = document.getElementById("originalFiles");
  const originalFolderInput = document.getElementById("originalFolderFiles");
  const convertedInput = document.getElementById("convertedFiles");
  const convertedFolderInput = document.getElementById("convertedFolderFiles");
    
  originalInput.value = "";
  if (originalFolderInput) originalFolderInput.value = "";
  convertedInput.value = "";
  if (convertedFolderInput) convertedFolderInput.value = "";
  resultsDiv.innerHTML = "";

    try {
      const response = await fetch("/api/delete-folder", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folderId: "sample_data" })
      });

      const result = await response.json();
  } catch (error) {
      console.error("Network error:", error);
  }
}

function handleClearS3Mode() {
  const resultsDiv = document.getElementById("results");
  const s3BucketInput = document.getElementById("s3Bucket");
  const s3OriginalPrefixInput = document.getElementById("s3OriginalPrefix");
  const s3ConvertedPrefixInput = document.getElementById("s3ConvertedPrefix");
  const dateStartInput = document.getElementById("dateRangeStart");
  const dateEndInput = document.getElementById("dateRangeEnd");

  s3BucketInput.value = "";
  s3OriginalPrefixInput.value = "";
  s3ConvertedPrefixInput.value = "";
  resultsDiv.innerHTML = "";
  
  // Reset date fields to defaults
  initializeS3DateDefaults();
}

  button.addEventListener("click", handleClearFileMode);

let chartInstances = {};

function destroyChartInstances() {
  for (const key in chartInstances) {
    if (chartInstances[key]) {
      try {
        chartInstances[key].destroy();
      } catch {}
      delete chartInstances[key];
    }
  }
}

function renderChartJsCharts(data) {
  if (typeof Chart === "undefined") return;

  destroyChartInstances();

  const findings = data.findings || [];
  if (findings.length === 0) return;

  const severityCounts = { HIGH: 0, MEDIUM: 0, LOW: 0 };
  const byChannelMap = new Map();
  const byDateMap = new Map();

  for (const finding of findings) {
    const locations = Array.isArray(finding.locations) && finding.locations.length > 0
      ? finding.locations
      : [{ file_ref: finding.file_ref, content_id: finding.content_id }];

    const rawSeverity = (finding.severity || "WARNING").toUpperCase();
    const severity = (rawSeverity === "ERROR" || rawSeverity === "HIGH") ? "HIGH" : ((rawSeverity === "INFO" || rawSeverity === "LOW") ? "LOW" : "MEDIUM");

    for (const loc of locations) {
      severityCounts[severity] = (severityCounts[severity] || 0) + 1;

      const fileRef = String(loc.file_ref || "").trim();
      const parsed = parseChannelFileRef(fileRef);
      const channel = parsed && parsed.channel ? parsed.channel : "General";
      const file = parsed && parsed.file ? parsed.file : fileRef;

      byChannelMap.set(channel, (byChannelMap.get(channel) || 0) + 1);

      const dateMatch = (file || fileRef).match(/\b(\d{4}-\d{2}-\d{2})\b/);
      if (dateMatch) {
        byDateMap.set(dateMatch[1], (byDateMap.get(dateMatch[1]) || 0) + 1);
      }
    }
  }

  // Channel Bar Chart
  const channelCanvas = document.getElementById("channelChartCanvas");
  if (channelCanvas) {
    const sortedCh = Array.from(byChannelMap.entries()).sort((a, b) => b[1] - a[1]).slice(0, 10);
    chartInstances.channel = new Chart(channelCanvas, {
      type: "bar",
      data: {
        labels: sortedCh.map((x) => x[0]),
        datasets: [{
          label: "Issues Count",
          data: sortedCh.map((x) => x[1]),
          backgroundColor: "rgba(26, 115, 232, 0.85)",
          borderColor: "#1a73e8",
          borderWidth: 1,
          borderRadius: 6
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false }
        },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.05)" } }
        },
        onClick: function(evt, activeEls) {
          if (activeEls.length > 0) {
            const idx = activeEls[0].index;
            applyDashboardFilter(sortedCh[idx][0], "Channel");
          }
        }
      }
    });
  }

  // 3. Date Trend Bar / Line Chart
  const dateCanvas = document.getElementById("dateChartCanvas");
  if (dateCanvas) {
    const sortedDt = Array.from(byDateMap.entries()).sort((a, b) => a[0].localeCompare(b[0])).slice(0, 14);
    chartInstances.date = new Chart(dateCanvas, {
      type: "bar",
      data: {
        labels: sortedDt.map((x) => x[0]),
        datasets: [{
          label: "Issues Count",
          data: sortedDt.map((x) => x[1]),
          backgroundColor: "rgba(242, 153, 0, 0.85)",
          borderColor: "#f29900",
          borderWidth: 1,
          borderRadius: 6
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false }
        },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.05)" } }
        },
        onClick: function(evt, activeEls) {
          if (activeEls.length > 0) {
            const idx = activeEls[0].index;
            applyDashboardFilter(sortedDt[idx][0], "Date");
          }
        }
      }
    });
  }
}

function buildQualityDashboard(data) {
  const findings = data.findings || [];
  if (findings.length === 0) {
    return "";
  }

  let totalOccurrences = 0;
  const severityCounts = { HIGH: 0, MEDIUM: 0, LOW: 0 };
  const byChannelMap = new Map();
  const byDateMap = new Map();
  const byErrorCodeMap = new Map();

  for (const finding of findings) {
    const locations = Array.isArray(finding.locations) && finding.locations.length > 0
      ? finding.locations
      : [{ file_ref: finding.file_ref, content_id: finding.content_id }];

    const rawSeverity = (finding.severity || "WARNING").toUpperCase();
    const severity = (rawSeverity === "ERROR" || rawSeverity === "HIGH") ? "HIGH" : ((rawSeverity === "INFO" || rawSeverity === "LOW") ? "LOW" : "MEDIUM");
    const code = String(finding.errorCode || finding.check_id || "UNKNOWN");
    const sampleMsg = getFindingDisplayMessage(finding);

    if (!byErrorCodeMap.has(code)) {
      byErrorCodeMap.set(code, {
        code,
        severity,
        count: 0,
        sampleMessage: sampleMsg,
      });
    }
    byErrorCodeMap.get(code).count += locations.length;

    for (const loc of locations) {
      totalOccurrences += 1;
      severityCounts[severity] = (severityCounts[severity] || 0) + 1;

      const fileRef = String(loc.file_ref || "").trim();
      const parsed = parseChannelFileRef(fileRef);
      const channel = parsed && parsed.channel ? parsed.channel : "General";
      const file = parsed && parsed.file ? parsed.file : fileRef;

      if (!byChannelMap.has(channel)) {
        byChannelMap.set(channel, { total: 0, HIGH: 0, MEDIUM: 0, LOW: 0, checkIds: {} });
      }
      const chStats = byChannelMap.get(channel);
      chStats.total += 1;
      chStats[severity] = (chStats[severity] || 0) + 1;
      chStats.checkIds[finding.check_id] = (chStats.checkIds[finding.check_id] || 0) + 1;

      const dateMatch = (file || fileRef).match(/\b(\d{4}-\d{2}-\d{2})\b/);
      const dateStr = dateMatch ? dateMatch[1] : null;
      if (dateStr) {
        if (!byDateMap.has(dateStr)) {
          byDateMap.set(dateStr, { date: dateStr, total: 0, HIGH: 0, MEDIUM: 0, LOW: 0 });
        }
        const dtStats = byDateMap.get(dateStr);
        dtStats.total += 1;
        dtStats[severity] = (dtStats[severity] || 0) + 1;
      }
    }
  }

  const sortedChannels = Array.from(byChannelMap.entries())
    .map(([channel, stats]) => ({ channel, ...stats }))
    .sort((a, b) => b.total - a.total);

  const sortedDates = Array.from(byDateMap.values())
    .sort((a, b) => a.date.localeCompare(b.date));

  const sortedErrorCodes = Array.from(byErrorCodeMap.values())
    .sort((a, b) => b.count - a.count);

  const maxChannelCount = sortedChannels[0] ? sortedChannels[0].total : 1;
  const channelBarsHtml = sortedChannels.slice(0, 10).map((item) => {
    const pctOfMax = Math.max(6, Math.round((item.total / maxChannelCount) * 100));
    const pctOfTotal = totalOccurrences > 0 ? ((item.total / totalOccurrences) * 100).toFixed(1) : "0";
    return `
      <div class="bar-row" onclick="applyDashboardFilter('${escapeHtml(item.channel)}', 'Channel')" title="Click to filter findings by ${escapeHtml(item.channel)}">
        <span class="bar-label">${escapeHtml(item.channel)}</span>
        <div class="bar-track">
          <div class="bar-fill bar-fill-primary" style="width: ${pctOfMax}%"></div>
        </div>
        <div class="bar-value-group">
          <span class="bar-count">${item.total}</span>
          <span class="bar-pct">(${pctOfTotal}%)</span>
        </div>
      </div>
    `;
  }).join("");

  const maxDateCount = sortedDates.length ? Math.max(...sortedDates.map((d) => d.total)) : 1;
  const dateBarsHtml = sortedDates.slice(0, 10).map((item) => {
    const pctOfMax = Math.max(6, Math.round((item.total / maxDateCount) * 100));
    const pctOfTotal = totalOccurrences > 0 ? ((item.total / totalOccurrences) * 100).toFixed(1) : "0";
    let dayLabel = item.date;
    try {
      const d = new Date(item.date);
      if (!isNaN(d.getTime())) {
        const dayName = d.toLocaleDateString("en-US", { weekday: "short" });
        dayLabel = `${item.date} (${dayName})`;
      }
    } catch {}

    return `
      <div class="bar-row" onclick="applyDashboardFilter('${escapeHtml(item.date)}', 'Date')" title="Click to filter findings by ${escapeHtml(item.date)}">
        <span class="bar-label">${escapeHtml(dayLabel)}</span>
        <div class="bar-track">
          <div class="bar-fill bar-fill-medium" style="width: ${pctOfMax}%"></div>
        </div>
        <div class="bar-value-group">
          <span class="bar-count">${item.total}</span>
          <span class="bar-pct">(${pctOfTotal}%)</span>
        </div>
      </div>
    `;
  }).join("");

  const coverage = data.analytics_field_coverage || {};
  const coverageFields = Array.isArray(coverage.fields) ? coverage.fields : [];
  const coverageChannels = Array.isArray(coverage.channels) ? coverage.channels : [];
  const channelRuleTableHtml = coverageChannels.length > 0 && coverageFields.length > 0 ? `
    <div class="analytics-card analytics-card-full">
      <div class="analytics-card-header">
        <div>
          <h3>📋 Missing Fields by Channel</h3>
          <span class="analytics-hint">Each cell shows how many records are missing that field.</span>
        </div>
        <button type="button" class="analytics-download-btn" onclick="downloadMissingFieldsCsv()">Download CSV</button>
      </div>
      <div class="channel-rule-table-wrap">
        <table class="channel-rule-table">
          <thead>
            <tr>
              <th scope="col">Channel</th>
              ${coverageFields.map((field) => `<th scope="col" title="${escapeHtml(field.label)}">${escapeHtml(field.label)}</th>`).join("")}
              <th scope="col">Records</th>
            </tr>
          </thead>
          <tbody>
            ${coverageChannels.map((item) => `
              <tr>
                <th scope="row">${escapeHtml(item.channel)}</th>
                ${coverageFields.map((field) => {
                  const stats = (item.fields || {})[field.label] || { checked: item.records || 0, passed: 0, failed: 0 };
                  const missingCount = Number(stats.failed) || 0;
                  const countClass = missingCount > 0 ? " channel-rule-failed" : " channel-rule-zero";
                  return `<td><span class="channel-rule-coverage${countClass}">${missingCount}</span></td>`;
                }).join("")}
                <td class="channel-rule-total">${item.records}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  ` : "";

  const channelCardHtml = sortedChannels.length > 0 ? `
    <div class="analytics-card">
      <div class="analytics-card-header">
        <h3>📺 Issues by Channel (${sortedChannels.length})</h3>
        <span class="analytics-hint">Click bar to filter findings</span>
      </div>
      <div class="chart-box">
        <canvas id="channelChartCanvas"></canvas>
      </div>
      <div class="bar-chart-list">
        ${channelBarsHtml}
      </div>
    </div>
  ` : "";

  const dateCardHtml = sortedDates.length > 0 ? `
    <div class="analytics-card">
      <div class="analytics-card-header">
        <h3>📅 Issues by Date (${sortedDates.length} day${sortedDates.length === 1 ? "" : "s"})</h3>
        <span class="analytics-hint">Click bar to filter findings</span>
      </div>
      <div class="chart-box">
        <canvas id="dateChartCanvas"></canvas>
      </div>
      <div class="bar-chart-list">
        ${dateBarsHtml}
      </div>
    </div>
  ` : "";

  return `
    <div class="dashboard-container">
      <div class="dashboard-header">
        <div class="dashboard-title-group">
          <span class="dashboard-badge">📊 Quality Dashboard</span>
          <h2>Quality Check Insights</h2>
        </div>
        <div id="dashboardFilterBar" class="dashboard-active-filter-bar hidden">
          <span>Active filter: <strong id="activeDashboardFilterName"></strong></span>
          <button type="button" class="dashboard-reset-filter-btn" onclick="clearDashboardFilter()">Clear ✕</button>
        </div>
      </div>

      <div class="kpi-grid">
        <div class="kpi-card kpi-total">
          <div class="kpi-icon">🔍</div>
          <div class="kpi-body">
            <span class="kpi-value">${totalOccurrences}</span>
            <span class="kpi-label">Total Issues (${sortedErrorCodes.length} type${sortedErrorCodes.length === 1 ? "" : "s"})</span>
          </div>
        </div>
        <div class="kpi-card kpi-high" onclick="applyDashboardFilter('high', 'Severity')" title="Click to filter High severity findings">
          <div class="kpi-icon">🔴</div>
          <div class="kpi-body">
            <span class="kpi-value">${severityCounts.HIGH || 0}</span>
            <span class="kpi-label">High Severity</span>
          </div>
        </div>
        <div class="kpi-card kpi-medium" onclick="applyDashboardFilter('medium', 'Severity')" title="Click to filter Medium severity findings">
          <div class="kpi-icon">🟠</div>
          <div class="kpi-body">
            <span class="kpi-value">${severityCounts.MEDIUM || 0}</span>
            <span class="kpi-label">Medium Severity</span>
          </div>
        </div>
        <div class="kpi-card kpi-low" onclick="applyDashboardFilter('low', 'Severity')" title="Click to filter Low severity findings">
          <div class="kpi-icon">🔵</div>
          <div class="kpi-body">
            <span class="kpi-value">${severityCounts.LOW || 0}</span>
            <span class="kpi-label">Low Severity</span>
          </div>
        </div>
      </div>

      <div class="analytics-grid">
        ${channelCardHtml}
        ${dateCardHtml}
        ${channelRuleTableHtml}
      </div>
    </div>
  `;
}

function applyDashboardFilter(filterValue, filterType) {
  const searchInput = document.getElementById("findingsSearchInput");
  const filterBar = document.getElementById("dashboardFilterBar");
  const filterNameSpan = document.getElementById("activeDashboardFilterName");

  if (searchInput) {
    searchInput.value = filterValue;
    filterFindingCards();
  }

  if (filterBar && filterNameSpan) {
    filterNameSpan.textContent = `${filterType}: ${filterValue}`;
    filterBar.classList.remove("hidden");
  }

  // Switch to Quality Checks tab to view filtered cards
  switchAppMainTab("checksTabPane");

  const toolbar = document.querySelector(".findings-toolbar");
  if (toolbar) {
    toolbar.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function clearDashboardFilter() {
  const searchInput = document.getElementById("findingsSearchInput");
  const filterBar = document.getElementById("dashboardFilterBar");

  if (searchInput) {
    searchInput.value = "";
    filterFindingCards();
  }

  if (filterBar) {
    filterBar.classList.add("hidden");
  }
}

function displayResults(data) {
  const resultsDiv = document.getElementById("results");
  let html = "";

  // Store globally so the dedicated Visual Analytics tab can render charts
  window.LAST_DATA_RESULTS = data;

  const analyticsTabContainer = document.getElementById("analyticsDashboardContainer");
  const analyticsBadgePill = document.getElementById("analyticsTabBadge");
  if (analyticsTabContainer && data.findings && data.findings.length > 0) {
    analyticsTabContainer.innerHTML = buildQualityDashboard(data);
    renderChartJsCharts(data);

    if (analyticsBadgePill) {
      analyticsBadgePill.textContent = data.findings.length;
      analyticsBadgePill.classList.remove("hidden");
    }
  } else if (analyticsTabContainer) {
    analyticsTabContainer.innerHTML = `
      <div class="analytics-empty-state">
        <div class="empty-icon">📊</div>
        <h3>No Quality Check Data Available Yet</h3>
        <p>Run a Quality Check to populate the analytics dashboard.</p>
      </div>
    `;
    if (analyticsBadgePill) {
      analyticsBadgePill.classList.add("hidden");
    }
  }

  // Summary
  const summary = data.summary || {};
  const totalRecords =
    (data.original_records_checked || 0) +
    (data.converted_records_checked || 0);

  html += `
        <div class="result-summary">
            <h3>✓ Check Complete</h3>
        ${data.date_range ? `<div class="summary-stat">
          <span class="label">S3 Load Date Range</span>
          <span class="value">${escapeHtml(data.date_range)}</span>
        </div>` : ''}
            <div class="summary-stat">
                <span class="label">Total Findings</span>
                <span class="value">${data.summary.total_findings}</span>
            </div>
            <div class="summary-stat">
              <span class="label">Records Analyzed</span>
                <span class="value">${totalRecords}</span>
            </div>
    `;

  if (data.original_records_checked) {
    html += `
            <div class="summary-stat">
                <span class="label">Original Records</span>
                <span class="value">${data.original_records_checked}</span>
            </div>
        `;
  }

  if (data.converted_records_checked) {
    html += `
            <div class="summary-stat">
                <span class="label">Converted Records</span>
                <span class="value">${data.converted_records_checked}</span>
            </div>
        `;
  }

  if (summary.by_severity || summary.by_check) {
    html += '<div class="summary-breakdown">';

    if (summary.by_severity) {
      Object.entries(summary.by_severity).forEach(([severity, count]) => {
        html += `
                    <div class="breakdown-item">
                        <div class="label">${severity.charAt(0).toUpperCase() + severity.slice(1)}</div>
                        <div class="value">${count}</div>
                    </div>
                `;
      });
    }

    html += "</div>";
  }

  html += "</div>";

  // Findings grouped by checker scope (original vs converted)
  if (data.findings && data.findings.length > 0) {
    // Store raw findings globally; grouping is applied at render/copy time.
    window.LAST_FINDINGS = data.findings;
    window.LAST_SUMMARY = data.summary;
    window.LAST_CONTENT_TITLES = data.content_titles || {};

    const groupedFindings = groupFindingsByError(data.findings);
    
    // Add copy all / bulk-check / search toolbar
    html += `
      <div class="copy-all-section findings-toolbar">
        <button class="btn-copy-all" onclick="copyAllFindings()">
          Copy All Findings for Jira
        </button>
        <button id="downloadFindingsExcelBtn" class="btn-copy-all" onclick="downloadFindingsExcel()">
          📊 Download Excel
        </button>
        <button id="bulkCrossSourceCheckBtn" class="btn-copy-all" onclick="checkAllCrossSourceFindings()">
          🔍 Check All Cross-Source Findings
        </button>
        <input
          type="text"
          id="findingsSearchInput"
          class="findings-search-input"
          placeholder="Filter by error code, message, or content ID..."
          oninput="filterFindingCards()"
        />
      </div>
    `;
    
    const sourceFindings = groupedFindings.filter((f) => classifyFindingScope(f) === "source");
    const convertedFindings = groupedFindings.filter((f) => classifyFindingScope(f) === "conversion");
    const otherFindings = groupedFindings.filter((f) => classifyFindingScope(f) === "other");

    html += '<div class="findings-section">';

    if (sourceFindings.length > 0) {
      html += `<div class="findings-group findings-group-source"><h3 class="findings-heading"><span class="findings-heading-title">🧩 Source Quality Findings</span> <span class="findings-count">${sourceFindings.length}</span></h3>`;
      html += '<div class="finding-grid">';
      html += sourceFindings.map(buildFindingCard).join("");
      html += "</div>";
      html += "</div>";
    }

    if (convertedFindings.length > 0) {
      html += `<div class="findings-group findings-group-conversion"><h3 class="findings-heading"><span class="findings-heading-title">🔄 Conversion Validation Findings</span> <span class="findings-count">${convertedFindings.length}</span></h3>`;
      html += '<div class="finding-grid">';
      html += convertedFindings.map(buildFindingCard).join("");
      html += "</div>";
      html += "</div>";
    }

    if (otherFindings.length > 0) {
      html += `<div class="findings-group findings-group-other"><h3 class="findings-heading"><span class="findings-heading-title">📌 Other Findings</span> <span class="findings-count">${otherFindings.length}</span></h3>`;
      html += '<div class="finding-grid">';
      html += otherFindings.map(buildFindingCard).join("");
      html += "</div>";
      html += "</div>";
    }

    html += "</div>";
  } else {
    html +=
      '<div class="success-message">No findings detected. Metadata looks good.</div>';
  }

  resultsDiv.innerHTML = html;
  resultsDiv.classList.remove("hidden");
}

// Error codes where GraphQL/IG may serve the value from a *different*
// matched provider source (SVOD, deeplink-VOD, ...) even though this
// specific source is missing it — see checkCrossSourceForFinding().
// "ratio" checks parse the missing image ratio out of the finding message;
// "field" checks look at a fixed merged-metadata field (title/description/genre).
const CROSS_SOURCE_CHECK_CONFIG = {
  MISSING_16X9_IMAGE: { kind: "ratio" },
  MISSING_2X3_IMAGE: { kind: "ratio" },
  MISSING_TITLE: { kind: "field", field: "title" },
  MISSING_DESCRIPTION: { kind: "field", field: "description" },
  MISSING_GENRE: { kind: "field", field: "genre" },
  UNMAPPED_GENRE: { kind: "field", field: "genre" },
};
let imageCheckCardSeq = 0;

function extractRatioFromFindingMessage(message) {
  const match = String(message || "").match(/(\d+\s*:\s*\d+)/);
  return match ? match[1].replace(/\s+/g, "") : "";
}

// --- Cross-source check result cache (sessionStorage) --------------------
// Keyed by contentId+kind+param so re-rendering a report, re-running the
// same check, or the "check all" bulk button don't re-hit Inspector Gadget
// for IDs we've already resolved this session.
function crossSourceCacheKey(contentId, kind, param) {
  return `${contentId}::${kind}::${param}`;
}

function loadCrossSourceCache() {
  try {
    const raw = window.sessionStorage.getItem("crossSourceCheckCache");
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveCrossSourceCacheEntry(key, result) {
  const cache = loadCrossSourceCache();
  cache[key] = result;
  try {
    window.sessionStorage.setItem("crossSourceCheckCache", JSON.stringify(cache));
  } catch {
    // Ignore storage failures; in-memory result still renders this run.
  }
}

function renderCrossSourceRow(contentId, kind, param, data, ok) {
  const safeId = escapeHtml(contentId);
  if (!ok || (data && data.error)) {
    return `<div class="image-check-row image-check-row-error">⚠️ <strong>${safeId}</strong>: ${escapeHtml((data && data.error) || "Check failed.")}</div>`;
  }
  if (kind === "ratio") {
    if (data.found) {
      const adapters = (data.matched_adapters || []).join(", ") || "unknown adapter";
      return `<div class="image-check-row image-check-row-found">✅ <strong>${safeId}</strong>: ${escapeHtml(data.ratio_label)} image found via another matched source (${escapeHtml(adapters)}) — likely a false positive at the source level, safe to dismiss.</div>`;
    }
    return `<div class="image-check-row image-check-row-missing">❌ <strong>${safeId}</strong>: ${escapeHtml(data.ratio_label)} image not found in any matched source — this looks like a real gap.</div>`;
  }

  const fieldLabels = { title: "Title", description: "Description", genre: "Genre" };
  const fieldLabel = fieldLabels[param] || param;
  if (data.found) {
    const adapters = (data.matched_adapters || []).join(", ") || "unknown adapter";
    const preview = data.value_preview ? ` ("${escapeHtml(data.value_preview)}")` : "";
    return `<div class="image-check-row image-check-row-found">✅ <strong>${safeId}</strong>: ${fieldLabel} found via another matched source${preview} (${escapeHtml(adapters)}) — likely a false positive at the source level, safe to dismiss.</div>`;
  }
  return `<div class="image-check-row image-check-row-missing">❌ <strong>${safeId}</strong>: ${fieldLabel} not found in any matched source — this looks like a real gap.</div>`;
}

// Ensures we have a live IG access token, reusing the same cache/prompt flow
// as the grouping check (window.GROUPING_AUTH_CACHE, sessionStorage
// "groupingAuthCache") so the user only pastes a token once per session.
// Pass forcePrompt=true to force a fresh refresh-token prompt (used when a
// cached access token gets rejected with 401 mid-flight).
async function ensureCrossSourceAccessToken(forcePrompt) {
  loadGroupingAuthCache();
  const cache = window.GROUPING_AUTH_CACHE || {};
  const now = Date.now();

  if (!forcePrompt && cache.accessToken && Number(cache.expiresAt || 0) > now) {
    return { accessToken: cache.accessToken, refreshToken: cache.refreshToken };
  }

  let refreshToken = cache.refreshToken;
  if (forcePrompt || !refreshToken) {
    refreshToken = window.prompt(
      "Paste your Inspector Gadget refresh token to check this on IG:",
      refreshToken || "",
    );
    if (!refreshToken) {
      return null;
    }
  }

  const tokenForm = new FormData();
  tokenForm.append("refresh_token", refreshToken);
  const tokenResp = await fetch("/api/grouping-check/token", { method: "POST", body: tokenForm });
  const tokenData = await tokenResp.json();
  if (!tokenResp.ok || !tokenData.access_token) {
    throw new Error(tokenData.error || "Could not exchange refresh token.");
  }

  saveGroupingAuthCache({
    refreshToken,
    accessToken: tokenData.access_token,
    expiresAt: now + GROUPING_ACCESS_TOKEN_CACHE_MS,
  });
  return { accessToken: tokenData.access_token, refreshToken };
}

function buildFindingCard(finding) {
  const severityRaw = (finding.severity || "WARNING").toUpperCase();
  const severityClassMap = {
    ERROR: "high",
    WARNING: "medium",
    INFO: "low",
  };
  const severity = severityClassMap[severityRaw] || "medium";
  const displayCode = finding.errorCode || finding.check_id || "UNKNOWN";
  const displayMessage = getFindingDisplayMessage(finding);
  const invalidGenres = extractInvalidGenresFromMessage(finding.message || "");
  const invalidGenresHtml = invalidGenres.length
    ? `
      <div class="finding-tags" aria-label="Invalid mapped genres">
        ${invalidGenres.map((genre) => `<span class="finding-tag">${escapeHtml(genre)}</span>`).join("")}
      </div>
    `
    : "";
  const locations = Array.isArray(finding.locations)
    ? finding.locations
    : [{ file_ref: finding.file_ref, content_id: finding.content_id }];
  const uniqueFileRefs = Array.from(
    new Set(
      (locations || [])
        .map((loc) => (loc && loc.file_ref ? String(loc.file_ref).trim() : ""))
        .filter(Boolean),
    ),
  );
  const groupedByContent = groupLocationsByContentId(locations);
  const copyableContentIds = groupedByContent
    .map((entry) => (entry.content_id || "").toString().trim())
    .filter(Boolean);
  const cardCopyPayload = encodeURIComponent(
    JSON.stringify({
      severity: severityRaw,
      code: displayCode,
      message: displayMessage,
      contentIds: copyableContentIds,
      fileRefs: uniqueFileRefs,
    })
  );
  const encodedFileRefs = encodeURIComponent(JSON.stringify(uniqueFileRefs));
  const encodedContentIds = encodeURIComponent(JSON.stringify(copyableContentIds));
  // DOM node count per card must stay bounded regardless of how many files/content
  // IDs a finding occurs in (can be tens of thousands with large datasets) — only
  // a small preview is rendered up front, the rest expands on demand.
  const previewLimit = 12;
  const visibleFileRefs = uniqueFileRefs.slice(0, previewLimit);
  const hiddenFileRefs = uniqueFileRefs.slice(previewLimit);
  const visibleContentIds = groupedByContent.slice(0, previewLimit);
  const hiddenContentIds = groupedByContent.slice(previewLimit);
  const fileRefsHtml = visibleFileRefs
    .map((fileRef) => {
      const safe = escapeHtml(fileRef);
      const encodedFileRef = encodeURIComponent(JSON.stringify(fileRef));
      return `<button type="button" class="finding-chip" onclick='copyFileRef("${encodedFileRef}", this)' aria-label="Copy file ${safe}">${safe}</button>`;
    })
    .join("");
  const hiddenFileRefsHtml = hiddenFileRefs
    .map((fileRef) => {
      const safe = escapeHtml(fileRef);
      const encodedFileRef = encodeURIComponent(JSON.stringify(fileRef));
      return `<button type="button" class="finding-chip finding-chip-hidden" hidden onclick='copyFileRef("${encodedFileRef}", this)' aria-label="Copy file ${safe}">${safe}</button>`;
    })
    .join("");
  const fileRefsMoreButton = hiddenFileRefs.length
    ? `<button type="button" class="finding-chip finding-chip-more" onclick='toggleContentIdChips(this)' aria-expanded="false" data-more-count="${hiddenFileRefs.length}">+${hiddenFileRefs.length} more</button>`
    : "";
  const contentIdsHtml = visibleContentIds
    .filter((entry) => String(entry.content_id || "").trim())
    .map((entry) => {
      const contentId = escapeHtml(entry.content_id);
      const encodedContentId = encodeURIComponent(JSON.stringify(entry.content_id));
      return `<button type="button" class="finding-chip" onclick='copyContentId("${encodedContentId}", this)' aria-label="Copy content ID ${contentId}">${contentId}</button>`;
    })
    .join("");
  const hiddenContentIdsHtml = hiddenContentIds
    .filter((entry) => String(entry.content_id || "").trim())
    .map((entry) => {
      const contentId = escapeHtml(entry.content_id);
      const encodedContentId = encodeURIComponent(JSON.stringify(entry.content_id));
      return `<button type="button" class="finding-chip finding-chip-hidden" hidden onclick='copyContentId("${encodedContentId}", this)' aria-label="Copy content ID ${contentId}">${contentId}</button>`;
    })
    .join("");
  const moreButton = hiddenContentIds.length
    ? `<button type="button" class="finding-chip finding-chip-more" onclick='toggleContentIdChips(this)' aria-expanded="false" data-more-count="${hiddenContentIds.length}">+${hiddenContentIds.length} more</button>`
    : "";
  const locationTableHtml = buildLocationTable(locations, encodedContentIds, 25);
  const locationLines = `
    ${locationTableHtml || `<section class="finding-content-section" aria-label="File identifiers">
      <div class="finding-content-section-header">
        <span class="finding-meta-label">Files${uniqueFileRefs.length > previewLimit ? ` (${uniqueFileRefs.length})` : ""}</span>
        <button
          type="button"
          class="finding-copy-all-ids"
          onclick='copyAllFileRefs("${encodedFileRefs}", this)'
          title="Copy all file names"
          aria-label="Copy all file names"
        >
          Copy Files
        </button>
      </div>
      <div class="finding-chip-list">
        ${fileRefsHtml}${hiddenFileRefsHtml}${fileRefsMoreButton}
      </div>
    </section>
    <section class="finding-content-section" aria-label="Content IDs">
      <div class="finding-content-section-header">
        <span class="finding-meta-label">Content IDs</span>
        <button
          type="button"
          class="finding-copy-all-ids"
          onclick='copyAllContentIds("${encodedContentIds}", this)'
          title="Copy all content IDs"
          aria-label="Copy all content IDs"
        >
          Copy IDs
        </button>
      </div>
      <div class="finding-chip-list">
        ${copyableContentIds.length > 0 ? `${contentIdsHtml}${hiddenContentIdsHtml}${moreButton}` : '<span class="finding-meta-label">No content ID for this finding. Use file names above.</span>'}
      </div>
    </section>`}
  `;

  const isImageRatioFinding = Boolean(CROSS_SOURCE_CHECK_CONFIG[displayCode]);
  let imageCheckHtml = "";
  if (isImageRatioFinding) {
    const config = CROSS_SOURCE_CHECK_CONFIG[displayCode];
    const kind = config.kind;
    const param = kind === "ratio" ? extractRatioFromFindingMessage(displayMessage) : config.field;
    const cardId = `xsrc-check-${imageCheckCardSeq++}`;
    const encodedContentIdsForCheck = encodeURIComponent(JSON.stringify(copyableContentIds));
    const encodedParam = encodeURIComponent(JSON.stringify(param));
    imageCheckHtml = `
      <section class="finding-content-section finding-image-check" aria-label="Cross-source check">
        <div class="finding-content-section-header">
          <span class="finding-meta-label">Is this served by another source?</span>
          <button
            type="button"
            class="finding-copy-all-ids finding-image-check-btn cross-source-check-btn"
            ${param ? "" : "disabled"}
            data-card-id="${cardId}"
            data-content-ids="${encodedContentIdsForCheck}"
            data-param="${encodedParam}"
            data-kind="${kind}"
            onclick='checkCrossSourceForFinding("${cardId}", "${encodedContentIdsForCheck}", "${encodedParam}", "${kind}", this)'
            title="Check Inspector Gadget for this value from another provider source (SVOD, deeplink, ...)"
          >
            🔍 Check on IG (cross-source)
          </button>
        </div>
        <div id="${cardId}" class="finding-image-check-results"></div>
      </section>
    `;
  }

  const searchText = encodeURIComponent(
    `${severity} ${severityRaw} ${displayCode} ${displayMessage} ${copyableContentIds.join(" ")} ${uniqueFileRefs.join(" ")}`.toLowerCase(),
  );

  return `
        <article class="finding-item ${severity}" data-search-text="${searchText}">
            <div class="finding-item-header-row">
              <button type="button" class="finding-item-header" onclick='toggleFindingCard(this)' aria-expanded="true">
                  <span class="finding-check-id-wrapper">
                    <span class="finding-severity-dot ${severity}" aria-hidden="true"></span>
                    <span class="finding-check-id">${escapeHtml(displayCode)}</span>
                    <span class="finding-severity ${severity}">${severityRaw}</span>
                  </span>
                  <span class="finding-chevron" aria-hidden="true">⌄</span>
              </button>
              <button
                type="button"
                class="finding-copy-block-btn"
                onclick='copyFindingSummary("${cardCopyPayload}", this)'
                title="Copy this error block (for Jira/tickets)"
                aria-label="Copy this error block"
              >
                📋 Copy Block
              </button>
            </div>
            <div class="finding-card-body">
              <div class="finding-message">${escapeHtml(displayMessage)}</div>
              ${invalidGenresHtml}
              <div class="finding-meta">
                <div class="finding-occurrence-row"><span class="finding-meta-label">Occurrences:</span> ${locations.length}</div>
                ${locationLines}
                ${imageCheckHtml}
              </div>
            </div>
        </article>
    `;
}

async function checkCrossSourceForFinding(cardId, encodedContentIds, encodedParam, kind, buttonEl) {
  const resultsEl = document.getElementById(cardId);
  if (!resultsEl) return;

  let contentIds = [];
  let param = "";
  try {
    contentIds = JSON.parse(decodeURIComponent(encodedContentIds));
    param = JSON.parse(decodeURIComponent(encodedParam));
  } catch {
    resultsEl.innerHTML = '<div class="image-check-error">Could not read content IDs for this finding.</div>';
    return;
  }

  if (kind === "ratio" && !param) {
    resultsEl.innerHTML = '<div class="image-check-error">Could not detect the missing ratio from this finding\'s message.</div>';
    return;
  }
  if (contentIds.length === 0) {
    resultsEl.innerHTML = '<div class="image-check-error">No content IDs available for this finding.</div>';
    return;
  }

  let auth;
  try {
    auth = await ensureCrossSourceAccessToken(false);
  } catch (err) {
    resultsEl.innerHTML = `<div class="image-check-error">${escapeHtml(err.message)}</div>`;
    return;
  }
  if (!auth) {
    return; // user cancelled the token prompt
  }

  if (buttonEl) {
    buttonEl.disabled = true;
    buttonEl.textContent = "Checking...";
  }
  resultsEl.innerHTML = '<div class="image-check-pending">Checking Inspector Gadget...</div>';

  const countryCode = String(window.SELECTED_COUNTRY_CODE || "SE").toUpperCase();
  let accessToken = auth.accessToken;

  // Runs one pass over all content ids. Throws {retry401: true} the first
  // time IG rejects the access token, so the caller can silently re-auth
  // and retry once instead of just dumping a raw 401 error on the user.
  const runOnce = async (allowRetry401) => {
    const rows = [];
    for (const contentId of contentIds) {
      const cacheKey = crossSourceCacheKey(contentId, kind, param);
      const cached = loadCrossSourceCache()[cacheKey];
      if (cached) {
        rows.push({ contentId, data: cached, ok: true });
        continue;
      }

      const form = new FormData();
      form.append("access_token", accessToken);
      form.append("content_id", contentId);
      form.append("country_code", countryCode);
      if (kind === "ratio") {
        form.append("ratio", param);
      } else {
        form.append("field", param);
      }

      const resp = await fetch("/api/check/image-source", { method: "POST", body: form });
      const data = await resp.json();

      if (resp.status === 401 && allowRetry401) {
        const err = new Error("401");
        err.retry401 = true;
        throw err;
      }

      if (resp.ok && !data.error) {
        saveCrossSourceCacheEntry(cacheKey, data);
      }
      rows.push({ contentId, data, ok: resp.ok });
    }
    return rows;
  };

  try {
    let rows;
    try {
      rows = await runOnce(true);
    } catch (err) {
      if (err && err.retry401) {
        const reauth = await ensureCrossSourceAccessToken(true);
        if (!reauth) {
          resultsEl.innerHTML = '<div class="image-check-error">Re-authentication cancelled.</div>';
          return;
        }
        accessToken = reauth.accessToken;
        rows = await runOnce(false);
      } else {
        throw err;
      }
    }

    resultsEl.innerHTML = rows
      .map(({ contentId, data, ok }) => renderCrossSourceRow(contentId, kind, param, data, ok))
      .join("");
  } catch (err) {
    resultsEl.innerHTML = `<div class="image-check-error">Network error while checking Inspector Gadget: ${escapeHtml(err.message)}</div>`;
  } finally {
    if (buttonEl) {
      buttonEl.disabled = false;
      buttonEl.textContent = "🔍 Check on IG (cross-source)";
    }
  }
}

// Bulk-runs every visible, not-yet-disabled cross-source check button on the
// page with a small concurrency cap, so users don't have to click each
// finding card individually.
async function checkAllCrossSourceFindings() {
  const buttons = Array.from(document.querySelectorAll(".cross-source-check-btn")).filter(
    (btn) => !btn.disabled,
  );
  if (buttons.length === 0) {
    alert("No cross-source-checkable findings on this page.");
    return;
  }

  const bulkBtn = document.getElementById("bulkCrossSourceCheckBtn");
  if (bulkBtn) {
    bulkBtn.disabled = true;
    bulkBtn.textContent = `Checking 0/${buttons.length}...`;
  }

  const concurrency = 4;
  let completed = 0;
  let index = 0;

  const runNext = async () => {
    while (index < buttons.length) {
      const myIndex = index;
      index += 1;
      const btn = buttons[myIndex];
      try {
        await checkCrossSourceForFinding(
          btn.dataset.cardId,
          btn.dataset.contentIds,
          btn.dataset.param,
          btn.dataset.kind,
          btn,
        );
      } catch {
        // Per-finding errors are already rendered inline; keep going.
      }
      completed += 1;
      if (bulkBtn) {
        bulkBtn.textContent = `Checking ${completed}/${buttons.length}...`;
      }
    }
  };

  await Promise.all(Array.from({ length: concurrency }, runNext));

  if (bulkBtn) {
    bulkBtn.disabled = false;
    bulkBtn.textContent = "🔍 Check All Cross-Source Findings";
  }
}

// Client-side filter for the findings list — matches error code, message,
// and content IDs against the search box above the findings section.
function filterFindingCards() {
  const input = document.getElementById("findingsSearchInput");
  if (!input) return;
  const query = input.value.trim().toLowerCase();

  const filterBar = document.getElementById("dashboardFilterBar");
  if (!query && filterBar) {
    filterBar.classList.add("hidden");
  }

  document.querySelectorAll(".finding-item").forEach((card) => {
    if (!query) {
      card.style.display = "";
      return;
    }
    let text = "";
    try {
      text = decodeURIComponent(card.dataset.searchText || "");
    } catch {
      text = card.dataset.searchText || "";
    }
    card.style.display = text.includes(query) ? "" : "none";
  });

  document.querySelectorAll(".findings-group").forEach((group) => {
    const anyVisible = Array.from(group.querySelectorAll(".finding-item")).some(
      (card) => card.style.display !== "none",
    );
    group.style.display = anyVisible ? "" : "none";
  });
}

function toggleFindingCard(headerButton) {
  const card = headerButton.closest(".finding-item");
  const body = card ? card.querySelector(".finding-card-body") : null;
  const expanded = headerButton.getAttribute("aria-expanded") === "true";

  headerButton.setAttribute("aria-expanded", expanded ? "false" : "true");

  if (body) {
    body.hidden = expanded;
  }
}

function toggleContentIdChips(buttonEl) {
  const section = buttonEl.closest(".finding-content-section");
  if (!section) {
    return;
  }

  const hiddenChips = Array.from(section.querySelectorAll(".finding-chip-hidden"));
  if (hiddenChips.length === 0) {
    return;
  }

  const expanded = buttonEl.getAttribute("aria-expanded") === "true";
  const nextExpanded = !expanded;

  hiddenChips.forEach((chip) => {
    chip.hidden = !nextExpanded;
  });

  buttonEl.setAttribute("aria-expanded", String(nextExpanded));
  buttonEl.textContent = nextExpanded
    ? "Show less"
    : `+${buttonEl.dataset.moreCount || hiddenChips.length} more`;
}

function copyFindingSummary(encodedPayload, buttonEl) {
  let payload;
  try {
    payload = JSON.parse(decodeURIComponent(encodedPayload || ""));
  } catch {
    return;
  }

  const text = [
    `[${payload.severity || "WARNING"}] ${payload.code || "UNKNOWN"}`,
    `Message: ${payload.message || ""}`,
    "Content IDs:",
    ...((payload.contentIds || []).length ? payload.contentIds.map((id) => `- ${id || "-"}`) : ["- (none)"]),
    "Files:",
    ...((payload.fileRefs || []).length ? payload.fileRefs.map((ref) => `- ${ref || "-"}`) : ["- (none)"]),
  ].join("\n");

  navigator.clipboard.writeText(text).then(() => {
    flashCopyFeedback(buttonEl, "✓ Copied");
  }).catch(() => {
    console.error("Failed to copy finding summary");
  });
}

function groupFindingsByError(findings) {
  const groups = new Map();

  for (const finding of findings || []) {
    const key = getFindingGroupingKey(finding);

    if (!groups.has(key)) {
      groups.set(key, {
        ...finding,
        locations: [],
        message_variants: [],
      });
    }

    const bucket = groups.get(key);
    bucket.locations.push({
      file_ref: finding.file_ref || "-",
      content_id: finding.content_id || "",
      message: finding.message || "",
    });
    if (finding.message) {
      bucket.message_variants.push(finding.message);
    }
  }

  const dedupeLocations = (locations) => {
    const seen = new Set();
    const out = [];
    for (const loc of locations || []) {
      const id = `${loc.file_ref || "-"}||${loc.content_id || ""}||${loc.message || ""}`;
      if (seen.has(id)) {
        continue;
      }
      seen.add(id);
      out.push(loc);
    }
    return out;
  };

  return Array.from(groups.values()).map((group) => {
    const uniqueLocations = dedupeLocations(group.locations);
    const messageVariants = Array.from(new Set(group.message_variants || []));
    return {
      ...group,
      message: messageVariants.length === 1
        ? messageVariants[0]
        : getCanonicalFindingMessage(group, messageVariants[0]),
      message_variants: messageVariants,
      locations: uniqueLocations,
      file_ref: uniqueLocations[0] ? uniqueLocations[0].file_ref : group.file_ref,
      content_id: uniqueLocations[0] ? uniqueLocations[0].content_id : group.content_id,
    };
  });
}

function getFindingGroupingKey(finding) {
  const checkId = (finding.check_id || "").toString();
  const severity = (finding.severity || "").toString().toUpperCase();
  const code = (finding.errorCode || finding.check_id || "UNKNOWN").toString();

  if (isUngroupedEpisodesAlert(finding)) {
    return ["UNGROUPED_EPISODES", checkId, severity].join("||");
  }

  return [code, checkId, severity].join("||");
}

function isUngroupedEpisodesAlert(finding) {
  const id = (finding.check_id || "").toString().toLowerCase();
  return id === "conv_ungrouped_episodes_by_series" || id === "orig_ungrouped_episodes_by_series";
}

function getFindingDisplayMessage(finding) {
  if (isUngroupedEpisodesAlert(finding)) {
    return "Episodes should be grouped under a single series.";
  }
  return finding.message || "";
}

function getCanonicalFindingMessage(finding, sampleMessage) {
  const checkId = (finding.check_id || "").toString();
  const code = (finding.errorCode || finding.check_id || "UNKNOWN").toString();
  const message = (sampleMessage || finding.message || "").toString();

  if (checkId.endsWith("_epg_from_to_dates_invalid") && message.includes("positive duration")) {
    return "Broadcast must have positive duration.";
  }
  if (checkId.endsWith("_epg_from_to_dates_invalid") && message.includes("to must be after from")) {
    return "EPG range must have an end time after the start time.";
  }
  if (checkId.endsWith("_broadcast_required_field_missing")) {
    const match = message.match(/Missing required broadcast field '([^']+)'/);
    return match ? `Missing required broadcast field '${match[1]}'.` : message;
  }

  return `${code} occurs across multiple broadcasts.`;
}

function parseChannelFileRef(fileRef) {
  let text = String(fileRef || "").trim();
  if (text.startsWith("File: ")) {
    text = text.slice(6).trim();
  }
  text = text
    .split(" | Broadcast: ")[0]
    .split(" | content: ")[0]
    .split(" | series: ")[0]
    .split(" | episode: ")[0]
    .split(" | record: ")[0]
    .trim();
  const parts = text.split(" / ");
  if (parts.length >= 2) {
    return {
      channel: parts[0].trim(),
      file: parts.slice(1).join(" / ").trim(),
    };
  }

  const pathParts = text.replaceAll("\\", "/").split("/").filter(Boolean);
  if (pathParts.length >= 2) {
    const file = pathParts[pathParts.length - 1];
    const channel = pathParts[pathParts.length - 2]
      .replace(/^original_/, "")
      .replace(/^converted_/, "")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
    return { channel, file };
  }

  return null;
}

function buildLocationTable(locations, encodedContentIds, previewLimit) {
  const byFile = new Map();
  for (const loc of locations || []) {
    const parsed = parseChannelFileRef(loc.file_ref);
    if (!parsed) {
      return "";
    }

    const key = `${parsed.channel}||${parsed.file}`;
    if (!byFile.has(key)) {
      byFile.set(key, {
        ...parsed,
        contentIds: [],
      });
    }

    const contentId = String(loc.content_id || "").trim();
    if (contentId && !byFile.get(key).contentIds.includes(contentId)) {
      byFile.get(key).contentIds.push(contentId);
    }
  }

  const rows = Array.from(byFile.values());

  if (rows.length === 0) {
    return "";
  }

  const byChannel = new Map();
  for (const row of rows) {
    const channel = row.channel || "-";
    if (!byChannel.has(channel)) {
      byChannel.set(channel, []);
    }
    byChannel.get(channel).push(row);
  }

  const channels = Array.from(byChannel.entries()).map(([channel, fileRows]) => ({
    channel,
    fileRows,
    contentCount: fileRows.reduce((total, row) => total + row.contentIds.length, 0),
  }));
  const visibleChannels = channels;
  const renderContentIds = (ids) => {
    if (ids.length === 0) {
      return `<div class="finding-id-list"><span class="finding-id-empty">No content ID</span></div>`;
    }
    return `
      <div class="finding-id-list">
        ${ids.map((id) => `<button type="button" class="finding-id-line" onclick='copyContentId("${encodeURIComponent(JSON.stringify(id))}", this)' aria-label="Copy content ID ${escapeHtml(id)}">${escapeHtml(id)}</button>`).join("")}
      </div>
    `;
  };
  const renderFileCard = (row, rowIndex) => `
    <div class="finding-file-card">
      <button type="button" class="finding-file-card-toggle" onclick="toggleFileIds(this)" aria-expanded="false">
        <span class="finding-file-card-main">
          <span class="finding-file-name">${escapeHtml(row.file || "-")}</span>
        </span>
        <span class="finding-file-card-side">
          <span class="finding-file-count">${row.contentIds.length} ID${row.contentIds.length === 1 ? "" : "s"}</span>
          <span class="finding-chevron" aria-hidden="true">⌄</span>
        </span>
      </button>
      <div class="finding-file-card-ids" hidden aria-label="Content IDs for ${escapeHtml(row.file || "file")}">
        ${renderContentIds(row.contentIds)}
      </div>
    </div>
  `;
  const renderChannel = (channelGroup, hidden, channelIndex) => `
    <div class="finding-location-channel${hidden ? " finding-location-row-hidden" : ""}"${hidden ? " hidden" : ""}>
      <button type="button" class="finding-location-channel-title" onclick="toggleLocationChannel(this)" aria-expanded="false">
        <span>${escapeHtml(channelGroup.channel)}</span>
        <span>
          ${channelGroup.fileRows.length} file${channelGroup.fileRows.length === 1 ? "" : "s"}, ${channelGroup.contentCount} ID${channelGroup.contentCount === 1 ? "" : "s"}
          <span class="finding-chevron" aria-hidden="true">⌄</span>
        </span>
      </button>
      <div class="finding-location-file-list" hidden>
        ${channelGroup.fileRows.map((row, rowIndex) => renderFileCard(row, `${channelIndex}-${rowIndex}`)).join("")}
      </div>
    </div>
  `;

  return `
    <section class="finding-content-section finding-location-table-section" aria-label="Affected content table">
      <div class="finding-content-section-header">
        <span class="finding-meta-label">Affected content (${locations.length}) in ${channels.length} channel${channels.length === 1 ? "" : "s"}</span>
        <button
          type="button"
          class="finding-copy-all-ids"
          onclick='copyAllContentIds("${encodedContentIds}", this)'
          title="Copy all content IDs"
          aria-label="Copy all content IDs"
        >
          Copy IDs
        </button>
      </div>
      <div class="finding-location-table">
        ${visibleChannels.map((channel, index) => renderChannel(channel, false, index)).join("")}
      </div>
    </section>
  `;
}

function toggleLocationChannel(buttonEl) {
  const channel = buttonEl.closest(".finding-location-channel");
  const fileList = channel ? channel.querySelector(".finding-location-file-list") : null;
  if (!fileList) {
    return;
  }

  const expanded = buttonEl.getAttribute("aria-expanded") === "true";
  const nextExpanded = !expanded;
  buttonEl.setAttribute("aria-expanded", String(nextExpanded));
  fileList.hidden = !nextExpanded;
}

function toggleFileIds(buttonEl) {
  const card = buttonEl.closest(".finding-file-card");
  const panel = card ? card.querySelector(".finding-file-card-ids") : null;
  if (!panel) {
    return;
  }

  const expanded = buttonEl.getAttribute("aria-expanded") === "true";
  const nextExpanded = !expanded;
  buttonEl.setAttribute("aria-expanded", String(nextExpanded));
  panel.hidden = !nextExpanded;
}

function normalizeFindingMessageForGrouping(message) {
  if (!message) {
    return "";
  }

  return message
    .toString()
    .replace(/deeplink-[a-z0-9.-]+/gi, "<CONTENT_ID>")
    .replace(/\b[a-z0-9._-]+\.json\b/gi, "<FILE>")
    .trim();
}

function groupLocationsByContentId(locations) {
  const byContent = new Map();

  for (const loc of locations || []) {
    const contentId = (loc.content_id || "").toString();
    const fileRef = (loc.file_ref || "-").toString();

    if (!byContent.has(contentId)) {
      byContent.set(contentId, new Set());
    }
    byContent.get(contentId).add(fileRef);
  }

  return Array.from(byContent.entries()).map(([content_id, files]) => ({
    content_id,
    files: Array.from(files),
  }));
}

function extractInvalidGenresFromMessage(message) {
  if (!message || !message.toLowerCase().includes("outside internal contract")) {
    return [];
  }

  const bracketMatch = message.match(/outside internal contract:\s*\[(.*?)\]/i);
  if (bracketMatch && bracketMatch[1]) {
    return bracketMatch[1]
      .split(",")
      .map((part) => part.trim().replace(/^['\"]+|['\"]+$/g, ""))
      .filter(Boolean);
  }

  const tail = message.split(":").slice(1).join(":").trim();
  if (!tail) {
    return [];
  }

  return (tail.match(/[A-Z][A-Z0-9_]{2,}/g) || [])
    .map((part) => part.trim().replace(/^['\"]+|['\"]+$/g, ""))
    .filter(Boolean)
    .filter((value) => value !== "CONVERTED" && value !== "GENRES" && value !== "OUTSIDE" && value !== "INTERNAL" && value !== "CONTRACT");
}

function showLoading(show) {
  const loadingDiv = document.getElementById("loading");
  if (show) {
    loadingDiv.classList.remove("hidden");
  } else {
    loadingDiv.classList.add("hidden");
  }
}

function setLoadingMessage(message) {
  const text = document.querySelector("#loading p");
  if (text) {
    text.textContent = message;
  }
}

function hideResults() {
  const resultsDiv = document.getElementById("results");
  resultsDiv.classList.add("hidden");
}

function showError(message) {
  const resultsDiv = document.getElementById("results");
  const timestamp = new Date().toISOString();
  const errorId = `ERR-${Date.now()}`;
  const lower = String(message || "").toLowerCase();
  
  let errorTitle = "Validation Error";
  let suggestion = "";

  const isAwsAuthError =
    lower.includes("error when retrieving token from sso") ||
    lower.includes("token has expired and refresh failed") ||
    lower.includes("credentials have changed") ||
    lower.includes("session has expired") ||
    lower.includes("aws login") ||
    lower.includes("botocore[crt]");

  const isProviderMissingError =
    lower.includes("provider_id is required") ||
    lower.includes("please select a provider");

  const isFileSelectionError =
    lower.includes("no files uploaded") ||
    lower.includes("no files selected") ||
    lower.includes("at least one") && lower.includes("file");
  
  if (isAwsAuthError) {
    errorTitle = "AWS Authentication Required";
    suggestion = "Reauthenticate AWS credentials and retry. Run 'aws sso login --profile inspector-gadget-sandbox' for the configured profile, then run 'aws login' (or your company equivalent) for default credentials.";
  } else if (isProviderMissingError) {
    errorTitle = "Provider Selection Required";
    suggestion = "Please select a provider from the dropdown before running checks.";
  } else if (isFileSelectionError) {
    errorTitle = "File Selection Error";
    suggestion = "Ensure at least one metadata JSON file is selected.";
  } else if (lower.includes("parse")) {
    errorTitle = "Invalid JSON Format";
    suggestion = "Check that all uploaded files are valid JSON. Look for syntax errors like trailing commas or unescaped quotes.";
  } else if (lower.includes("server returned")) {
    errorTitle = "Server Communication Error";
    suggestion = "The server responded unexpectedly. This may be a temporary issue. Try again or check server logs.";
  }
  
  const html = `
    <div class="error-message">
      <div class="error-header">
        <h3>${escapeHtml(errorTitle)}</h3>
        <span class="error-id">${errorId}</span>
      </div>
      <div class="error-details">
        <div class="error-message-text">
          <strong>Error Details:</strong><br>
          ${escapeHtml(message)}
        </div>
        ${suggestion ? `<div class="error-suggestion"><strong>Suggestion:</strong><br>${escapeHtml(suggestion)}</div>` : ""}
        <div class="error-meta">
          <small><strong>Timestamp:</strong> ${timestamp}</small><br>
          <small><strong>Provider:</strong> ${escapeHtml(window.SELECTED_PROVIDER_NAME || "Not selected")}</small>
        </div>
      </div>
    </div>
  `;
  
  resultsDiv.innerHTML = html;
  resultsDiv.classList.remove("hidden");
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function formatFindingFileRef(fileRef) {
  let raw = String(fileRef || "");

  // Strip internal record-type prefixes added by the backend (e.g. "original_foo.json" → "foo.json")
  raw = raw.replace(/^(original|converted)_/, "");

  if (!raw.includes("|") || !raw.includes(":")) {
    return escapeHtml(raw);
  }

  const segments = raw.split("|").map((part) => part.trim()).filter(Boolean);
  const htmlSegments = segments.map((segment) => {
    const idx = segment.indexOf(":");
    if (idx <= 0) {
      return escapeHtml(segment);
    }
    const label = segment.slice(0, idx).trim();
    const value = segment.slice(idx + 1).trim();
    return `<strong>${escapeHtml(label)}:</strong> ${escapeHtml(value)}`;
  });
  return htmlSegments.join(" | ");
}

function copyToClipboard(text) {
  const decoded = text.replace(/\\'/g, "'").replace(/\\"/g, '"').replace(/\\n/g, '\n');
  navigator.clipboard.writeText(decoded).then(() => {
    alert("✓ Copied to clipboard!");
  }).catch(() => {
    alert("Failed to copy. Please try again.");
  });
}

function flashCopyFeedback(buttonEl, label = "Copied!") {
  if (!buttonEl) {
    return;
  }

  const resetLabel = buttonEl.dataset.resetLabel || buttonEl.textContent.trim();
  buttonEl.dataset.resetLabel = resetLabel;
  buttonEl.textContent = label;
  buttonEl.classList.add("is-copied");

  clearTimeout(buttonEl._copyFeedbackTimer);
  buttonEl._copyFeedbackTimer = window.setTimeout(() => {
    buttonEl.textContent = buttonEl.dataset.resetLabel || resetLabel;
    buttonEl.classList.remove("is-copied");
  }, 1200);
}

function copyContentId(encodedContentId, buttonEl) {
  let contentId = "";

  try {
    contentId = JSON.parse(decodeURIComponent(encodedContentId || ""));
  } catch {
    contentId = String(encodedContentId || "");
  }

  const text = String(contentId || "").trim();
  if (!text) {
    return;
  }

  navigator.clipboard.writeText(text).then(() => {
    flashCopyFeedback(buttonEl, "Copied!");
  }).catch(() => {
    console.error("Failed to copy content ID");
  });
}

function copyAllContentIds(encodedIds, buttonEl) {
  let parsedIds = [];

  try {
    parsedIds = JSON.parse(decodeURIComponent(encodedIds || ""));
  } catch {
    console.error("Failed to parse content IDs");
    return;
  }

  const ids = (parsedIds || [])
    .map((id) => String(id || "").trim())
    .filter(Boolean);

  if (ids.length === 0) {
    return;
  }

  navigator.clipboard.writeText(ids.join("\n")).then(() => {
    flashCopyFeedback(buttonEl, "Copied!");
  }).catch(() => {
    console.error("Failed to copy content IDs");
  });
}

function copyFileRef(encodedFileRef, buttonEl) {
  let fileRef = "";

  try {
    fileRef = JSON.parse(decodeURIComponent(encodedFileRef || ""));
  } catch {
    fileRef = String(encodedFileRef || "");
  }

  const text = String(fileRef || "").trim();
  if (!text) {
    return;
  }

  navigator.clipboard.writeText(text).then(() => {
    flashCopyFeedback(buttonEl, "Copied!");
  }).catch(() => {
    console.error("Failed to copy file reference");
  });
}

function copyAllFileRefs(encodedFileRefs, buttonEl) {
  let parsedRefs = [];

  try {
    parsedRefs = JSON.parse(decodeURIComponent(encodedFileRefs || ""));
  } catch {
    console.error("Failed to parse file references");
    return;
  }

  const refs = (parsedRefs || [])
    .map((ref) => String(ref || "").trim())
    .filter(Boolean);

  if (refs.length === 0) {
    return;
  }

  navigator.clipboard.writeText(refs.join("\n")).then(() => {
    flashCopyFeedback(buttonEl, "Copied!");
  }).catch(() => {
    console.error("Failed to copy file references");
  });
}

function crossSourceVerdictTextForFinding(finding, locations) {
  const displayCode = finding.errorCode || finding.check_id || "";
  const config = CROSS_SOURCE_CHECK_CONFIG[displayCode];
  if (!config) return "";

  const displayMessage = getFindingDisplayMessage(finding);
  const param = config.kind === "ratio" ? extractRatioFromFindingMessage(displayMessage) : config.field;
  if (!param) return "";

  const contentIds = Array.from(
    new Set(
      (locations || [])
        .flatMap((loc) => normalizeContentIdsForText(loc && loc.content_id))
        .filter(Boolean),
    ),
  );
  if (contentIds.length === 0) return "";

  const cache = loadCrossSourceCache();
  const lines = [];
  contentIds.forEach((contentId) => {
    const result = cache[crossSourceCacheKey(contentId, config.kind, param)];
    if (!result) return; // not checked yet — nothing to annotate
    if (result.error) {
      lines.push(`   Cross-source check (${contentId}): ⚠️ ${result.error}`);
    } else if (result.found) {
      const adapters = (result.matched_adapters || []).join(", ") || "unknown adapter";
      lines.push(`   Cross-source check (${contentId}): ✅ found via ${adapters} — likely a false positive, safe to dismiss.`);
    } else {
      lines.push(`   Cross-source check (${contentId}): ❌ not found in any matched source — real gap.`);
    }
  });

  return lines.length ? `${lines.join("\n")}\n` : "";
}

// Sends the raw (ungrouped) findings plus the content-id -> title map to the
// server, which builds an .xlsx workbook (one sheet per error code, columns
// File Name / Content Name / Content ID) and streams it back for download.
async function downloadFindingsExcel() {
  const findings = window.LAST_FINDINGS || [];
  if (findings.length === 0) {
    alert("No findings to export.");
    return;
  }

  const btn = document.getElementById("downloadFindingsExcelBtn");
  const previousLabel = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Building Excel...";
  }

  try {
    const resp = await fetch("/api/export/findings-excel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        findings,
        content_titles: window.LAST_CONTENT_TITLES || {},
      }),
    });

    if (!resp.ok) {
      let message = `Export failed (${resp.status}).`;
      try {
        const data = await resp.json();
        message = data.error || message;
      } catch {
        // response wasn't JSON; keep the generic message
      }
      alert(message);
      return;
    }

    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "metadata_findings.xlsx";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`Network error while exporting Excel: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = previousLabel;
    }
  }
}

function csvCell(value) {
  const text = String(value ?? "");
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

function downloadMissingFieldsCsv() {
  const data = window.LAST_DATA_RESULTS || {};
  const coverage = data.analytics_field_coverage || {};
  const fields = Array.isArray(coverage.fields) ? coverage.fields : [];
  const channels = Array.isArray(coverage.channels) ? coverage.channels : [];

  if (fields.length === 0 || channels.length === 0) {
    showError("No missing-fields table data is available to download");
    return;
  }

  const header = ["Channel", ...fields.map((field) => field.label), "Records"];
  const rows = channels.map((channel) => {
    const fieldCounts = fields.map((field) => {
      const stats = (channel.fields || {})[field.label] || { failed: 0 };
      return Number(stats.failed) || 0;
    });
    return [channel.channel, ...fieldCounts, channel.records || 0];
  });
  const csv = [header, ...rows]
    .map((row) => row.map(csvCell).join(","))
    .join("\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const provider = String(data.provider_id || "metadata").replace(/[^a-z0-9_-]+/gi, "-").toLowerCase();
  link.href = url;
  link.download = `${provider}-missing-fields-by-channel.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

window.downloadMissingFieldsCsv = downloadMissingFieldsCsv;

function copyAllFindings() {
  if (!window.LAST_FINDINGS || window.LAST_FINDINGS.length === 0) {
    alert("No findings to copy");
    return;
  }

  const findings = groupFindingsByError(window.LAST_FINDINGS);
  const summary = window.LAST_SUMMARY || {};
  const timestamp = new Date().toISOString();
  const provider = window.SELECTED_PROVIDER_NAME || "Unknown";

  let text = "METADATA QUALITY CHECK REPORT\n";
  text += "=".repeat(60) + "\n\n";
  
  text += `Generated: ${timestamp}\n`;
  text += `Provider: ${provider}\n`;
  text += `Total Findings: ${summary.total_findings || findings.length}\n\n`;

  if (summary.by_severity) {
    text += "Summary by Severity:\n";
    text += "-".repeat(40) + "\n";
    Object.entries(summary.by_severity).forEach(([severity, count]) => {
      text += `  ${severity.toUpperCase()}: ${count}\n`;
    });
    text += "\n";
  }

  text += "DETAILED FINDINGS\n";
  text += "=".repeat(60) + "\n\n";

  // Group findings by checker scope
  const sourceFindings = findings.filter((f) => classifyFindingScope(f) === "source");
  const convertedFindings = findings.filter((f) => classifyFindingScope(f) === "conversion");
  const otherFindings = findings.filter(
    (f) => classifyFindingScope(f) === "other",
  );

  if (sourceFindings.length > 0) {
    text += "SOURCE QUALITY FINDINGS\n";
    text += "-".repeat(40) + "\n";
    sourceFindings.forEach((finding, idx) => {
      const locations = Array.isArray(finding.locations)
        ? finding.locations
        : [{ file_ref: finding.file_ref, content_id: finding.content_id }];
      text += `\n${idx + 1}. [${(finding.severity || "").toUpperCase()}] ${finding.errorCode || finding.check_id}\n`;
      text += `   Message: ${getFindingDisplayMessage(finding)}\n`;
      text += `   Occurrences: ${locations.length}\n`;
      text += formatFindingContextForText(locations);
      text += crossSourceVerdictTextForFinding(finding, locations);
    });
    text += "\n\n";
  }

  if (convertedFindings.length > 0) {
    text += "CONVERSION VALIDATION FINDINGS\n";
    text += "-".repeat(40) + "\n";
    convertedFindings.forEach((finding, idx) => {
      const locations = Array.isArray(finding.locations)
        ? finding.locations
        : [{ file_ref: finding.file_ref, content_id: finding.content_id }];
      text += `\n${idx + 1}. [${(finding.severity || "").toUpperCase()}] ${finding.errorCode || finding.check_id}\n`;
      text += `   Message: ${getFindingDisplayMessage(finding)}\n`;
      text += `   Occurrences: ${locations.length}\n`;
      text += formatFindingContextForText(locations);
      text += crossSourceVerdictTextForFinding(finding, locations);
    });
    text += "\n";
  }

  if (otherFindings.length > 0) {
    text += "OTHER FINDINGS\n";
    text += "-".repeat(40) + "\n";
    otherFindings.forEach((finding, idx) => {
      const locations = Array.isArray(finding.locations)
        ? finding.locations
        : [{ file_ref: finding.file_ref, content_id: finding.content_id }];
      text += `\n${idx + 1}. [${(finding.severity || "").toUpperCase()}] ${finding.errorCode || finding.check_id}\n`;
      text += `   Message: ${getFindingDisplayMessage(finding)}\n`;
      text += `   Occurrences: ${locations.length}\n`;
      text += formatFindingContextForText(locations);
      text += crossSourceVerdictTextForFinding(finding, locations);
    });
    text += "\n";
  }

  text += "\n" + "=".repeat(60) + "\n";
  text += "End of Report\n";

  navigator.clipboard.writeText(text).then(() => {
    alert("✓ All findings copied to clipboard!");
  }).catch(() => {
    alert("Failed to copy. Please try again.");
  });
}

function formatFindingContextForText(locations) {
  const lines = [];

  const uniqueFiles = Array.from(
    new Set(
      (locations || [])
        .map((loc) => (loc && loc.file_ref ? String(loc.file_ref).trim() : ""))
        .filter(Boolean),
    ),
  );

  const uniqueContentIds = Array.from(
    new Set(
      (locations || [])
        .flatMap((loc) => normalizeContentIdsForText(loc && loc.content_id))
        .filter(Boolean),
    ),
  );

  const uniquePairs = Array.from(
    new Set(
      (locations || [])
        .flatMap((loc) => {
          const fileRef = loc && loc.file_ref ? String(loc.file_ref).trim() : "";
          if (!fileRef) {
            return [];
          }
          const ids = normalizeContentIdsForText(loc && loc.content_id);
          if (ids.length === 0) {
            return [`${fileRef}||-`];
          }
          return ids.map((contentId) => `${fileRef}||${contentId}`);
        })
        .filter(Boolean),
    ),
  );

  const occurrenceIdentifiers = (locations || []).flatMap((loc) => {
    const fileRef = loc && loc.file_ref ? String(loc.file_ref).trim() : "-";
    const ids = normalizeContentIdsForText(loc && loc.content_id);
    if (ids.length === 0) {
      return [`${fileRef} | -`];
    }
    return ids.map((contentId) => `${fileRef} | ${contentId}`);
  });

  lines.push(`   Files: ${uniqueFiles.length}`);
  uniqueFiles.forEach((fileRef) => {
    lines.push(`   File: ${fileRef}`);
  });

  if (uniqueContentIds.length > 0) {
    lines.push(`   Content IDs: ${uniqueContentIds.length}`);
    uniqueContentIds.forEach((contentId) => {
      lines.push(`   Content ID: ${contentId}`);
    });
  }

  if (uniquePairs.length > 0) {
    lines.push(`   File+Content Pairs: ${uniquePairs.length}`);
    uniquePairs.forEach((pair) => {
      const [fileRef, contentId] = pair.split("||");
      lines.push(`   Pair: ${fileRef} | ${contentId || "-"}`);
    });
  }

  if (occurrenceIdentifiers.length > 0) {
    lines.push(`   Occurrence Identifiers: ${occurrenceIdentifiers.length}`);
    occurrenceIdentifiers.forEach((identifier) => {
      lines.push(`   Identifier: ${identifier}`);
    });
  }

  return `${lines.join("\n")}\n`;
}

function normalizeContentIdsForText(value) {
  if (value == null) {
    return [];
  }

  if (Array.isArray(value)) {
    return value
      .flatMap((item) => normalizeContentIdsForText(item))
      .filter(Boolean);
  }

  const text = String(value).trim();
  if (!text) {
    return [];
  }

  // Some grouped payloads carry IDs as JSON-ish list strings. Parse them
  // so the exported report can show one content ID per line.
  if (text.startsWith("[") && text.endsWith("]")) {
    try {
      const parsed = JSON.parse(text.replace(/'/g, '"'));
      if (Array.isArray(parsed)) {
        return parsed
          .map((item) => String(item || "").trim())
          .filter(Boolean);
      }
    } catch {
      // Fall through to raw text handling.
    }
  }

  return [text];
}

function classifyFindingScope(finding) {
  const id = (finding.check_id || "").toUpperCase();
  if (id.startsWith("ORIG_") || id.startsWith("METADATA_ORIG_")) {
    return "source";
  }
  if (id.startsWith("CONV_") || id.startsWith("METADATA_CONV_")) {
    return "conversion";
  }
  return "other";
}

// =========================================================================
// Viaplay FI Grouping Checker — inline widget
// =========================================================================

function formatGroupingDebugInfo(debug) {
  if (!debug || typeof debug !== "object") {
    return "";
  }

  const summaryParts = [
    `files=${debug.files_total ?? 0}`,
    `json_ok=${debug.files_json_ok ?? 0}`,
    `json_failed=${debug.files_json_failed ?? 0}`,
    `episode_like=${debug.episode_like_records ?? 0}`,
    `missing_series_link=${debug.missing_series_link ?? 0}`,
    `missing_episode_id=${debug.missing_episode_id ?? 0}`,
    `episodes_found=${debug.episodes_found ?? 0}`,
  ];

  const reasonItems = Array.isArray(debug.sample_reasons) ? debug.sample_reasons : [];
  const reasonText = reasonItems
    .slice(0, 3)
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      const file = item.file || "<unknown>";
      const reason = item.reason || "unknown reason";
      return `${file}: ${reason}`;
    })
    .filter(Boolean)
    .join(" | ");

  return reasonText
    ? `${summaryParts.join(", ")} | sample_reasons: ${reasonText}`
    : summaryParts.join(", ");
}

async function submitGroupingRangeWithAdaptiveSplit({
  files,
  accessToken,
  refreshToken,
  countryCode,
  getAccessToken,
  setAccessToken,
  hasRetriedAuth = false,
  start,
  end,
  chunkIndex,
  totalChunks,
  setProgressTarget,
  getProgressPercent,
}) {
  const form = new FormData();
  if (accessToken) {
    form.append("access_token", accessToken);
  } else {
    form.append("refresh_token", refreshToken);
  }
  // Route lookup to the same country cache selected in the UI (FI/SE/NO).
  form.append("country_code", countryCode || "FI");
  for (let i = start; i < end; i += 1) {
    const file = files[i];
    form.append("files", file, file.name);
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), GROUPING_REQUEST_TIMEOUT_MS);
  let resp;
  let data;
  try {
    resp = await fetch("/api/grouping-check", {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    data = await parseApiResponse(resp);
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new Error(`Grouping request timed out after ${Math.round(GROUPING_REQUEST_TIMEOUT_MS / 1000)} seconds.`);
    }
    throw new Error(`Network error: ${err.message}`);
  } finally {
    window.clearTimeout(timeoutId);
  }

  if (resp.ok) {
    return [data];
  }

  if (!isPayloadTooLarge(resp.status, data)) {
    throw new Error(`Server error (${resp.status})${data && data.error ? `: ${data.error}` : ""}`);
  }

  if (end - start <= 1) {
    throw new Error(
      `Upload payload too large for file ${start + 1}. Split this source file before running grouping check.`,
    );
  }

  const mid = start + Math.floor((end - start) / 2);
  const left = await submitGroupingRangeWithAdaptiveSplit({
    files,
    accessToken,
    refreshToken,
    countryCode,
    getAccessToken,
    setAccessToken,
    hasRetriedAuth,
    start,
    end: mid,
    chunkIndex,
    totalChunks,
    setProgressTarget,
    getProgressPercent,
  });
  const right = await submitGroupingRangeWithAdaptiveSplit({
    files,
    accessToken,
    refreshToken,
    countryCode,
    getAccessToken,
    setAccessToken,
    hasRetriedAuth,
    start: mid,
    end,
    chunkIndex,
    totalChunks,
    setProgressTarget,
    getProgressPercent,
  });

  return [...left, ...right];
}

function setupGroupingChecker() {
  const runBtn = document.getElementById("runGroupingCheck");
  const clearBtn = document.getElementById("clearGroupingResults");
  if (!runBtn) return;
  runBtn.addEventListener("click", handleGroupingCheckAuto);
  if (clearBtn) clearBtn.addEventListener("click", clearGroupingResults);
}

function hasLocalGroupingFilesSelected() {
  const originalFiles = getSelectedUploadFiles("originalFiles", "originalFolderFiles");
  const convertedFiles = getSelectedUploadFiles("convertedFiles", "convertedFolderFiles");
  return originalFiles.length > 0 || convertedFiles.length > 0;
}

async function handleGroupingCheckAuto() {
  // Local files take precedence when present so uploaded-device checks never
  // accidentally run against S3.
  if (hasLocalGroupingFilesSelected()) {
    await handleGroupingCheck();
    return;
  }

  const mode = String(document.getElementById("modeSelect")?.value || "file").toLowerCase();
  if (mode === "s3") {
    await handleGroupingCheckS3();
    return;
  }

  await handleGroupingCheck();
}

async function resolveGroupingAccessToken(token) {
  const now = Date.now();
  const authCache = window.GROUPING_AUTH_CACHE || {};
  if (
    authCache.refreshToken === token
    && typeof authCache.accessToken === "string"
    && authCache.accessToken
    && Number(authCache.expiresAt || 0) > now
  ) {
    return authCache.accessToken;
  }

  const tokenForm = new FormData();
  tokenForm.append("refresh_token", token);
  const controller = new AbortController();
  const timeoutId = window.setTimeout(
    () => controller.abort(),
    GROUPING_TOKEN_EXCHANGE_TIMEOUT_MS,
  );
  let tokenResp;
  let tokenData;
  try {
    tokenResp = await fetch("/api/grouping-check/token", {
      method: "POST",
      body: tokenForm,
      signal: controller.signal,
    });
    tokenData = await parseApiResponse(tokenResp);
  } catch (error) {
    if (error && error.name === "AbortError") {
      throw new Error("Token exchange timed out after 45 seconds.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }

  if (!tokenResp.ok || !tokenData.access_token) {
    throw new Error(tokenData.error || "Could not exchange refresh token for an access token.");
  }

  const accessToken = tokenData.access_token;
  saveGroupingAuthCache({
    refreshToken: token,
    accessToken,
    expiresAt: now + GROUPING_ACCESS_TOKEN_CACHE_MS,
  });
  return accessToken;
}

async function handleGroupingCheck() {
  const token = getCurrentRefreshToken();
  if (!token) {
    showGroupingError("Please enter an Inspector Gadget refresh token.");
    return;
  }
  const countryCode = String(window.SELECTED_COUNTRY_CODE || "FI").toUpperCase();

  const originalFiles = getSelectedUploadFiles("originalFiles", "originalFolderFiles");
  const convertedFiles = getSelectedUploadFiles("convertedFiles", "convertedFolderFiles");

  // Grouping should use converted JSON whenever available; originals may be XML for other checks.
  const files = convertedFiles.length > 0 ? convertedFiles : originalFiles;
  const usingConvertedFiles = convertedFiles.length > 0;

  if (files.length === 0) {
    showGroupingError("Please upload converted Viaplay EPG JSON files in the Converted Metadata Files field first.");
    return;
  }

  if (!usingConvertedFiles) {
    const xmlFiles = files.filter((file) => /\.xml$/i.test((file && file.name) || ""));
    if (xmlFiles.length > 0) {
      const sampleNames = xmlFiles.slice(0, 3).map((file) => file.name).join(", ");
      showGroupingError(
        `Detected XML file(s): ${sampleNames}. Grouping check reads Converted Metadata Files (JSON). Upload converted JSON (for example se.viaplay.crime.YYYY-MM-DD.json) to the Converted Metadata Files input.`,
      );
      return;
    }
  }

  const loading = document.getElementById("groupingLoading");
  const loadingText = loading ? loading.querySelector("p") : null;
  const resultsDiv = document.getElementById("groupingResults");
  const clearBtn = document.getElementById("clearGroupingResults");
  const runBtn = document.getElementById("runGroupingCheck");

  resultsDiv.innerHTML = "";
  if (clearBtn) clearBtn.style.display = "none";
  loading.classList.remove("hidden");
  runBtn.disabled = true;
  if (loadingText) loadingText.textContent = "Preparing auth...";

  const failAuthAndReset = (message) => {
    showGroupingError(message);
    loading.classList.add("hidden");
    if (loadingText) loadingText.textContent = "Progress:";
    runBtn.disabled = false;
  };

  let accessToken = "";
  try {
    accessToken = await resolveGroupingAccessToken(token);
  } catch (err) {
    failAuthAndReset(`Network error while exchanging token: ${err.message}`);
    return;
  }

  const chunkRanges = buildGroupingChunkRanges(files);
  const totalChunks = chunkRanges.length;

  let firstError = null;
  const aggregated = {
    checked_at: null,
    total: 0,
    grouped: 0,
    ungrouped: 0,
    not_found: 0,
    errors: 0,
    pending_visibility: 0,
    ungrouped_by_series: {},
    all_results: [],
  };
  let displayedProgress = 0;
  let targetProgress = 0;
  let progressTimerId = null;

  const startProgressTicker = () => {
    if (progressTimerId !== null) {
      return;
    }

    progressTimerId = window.setInterval(() => {
      if (displayedProgress >= targetProgress) {
        return;
      }
      displayedProgress += 1;
      if (loadingText) {
        loadingText.textContent = `Progress: ${displayedProgress}%`;
      }
    }, 15);
  };

  const setProgressTarget = (percent) => {
    const clamped = Math.max(0, Math.min(100, Math.round(percent)));
    targetProgress = Math.max(targetProgress, clamped);
    startProgressTicker();
  };

  const waitForDisplayedProgress = (target) => {
    return new Promise((resolve) => {
      setProgressTarget(target);
      const waiter = window.setInterval(() => {
        if (displayedProgress >= target) {
          window.clearInterval(waiter);
          resolve();
        }
      }, 10);
    });
  };

  const stopProgressTicker = () => {
    if (progressTimerId !== null) {
      window.clearInterval(progressTimerId);
      progressTimerId = null;
    }
  };

  try {
    setProgressTarget(0);

    const parallelism = Math.min(GROUPING_PARALLEL_REQUESTS, totalChunks);
    let nextChunkIndex = 0;
    let completedChunks = 0;

    const worker = async () => {
      while (true) {
        const chunkIndex = nextChunkIndex;
        nextChunkIndex += 1;

        if (chunkIndex >= totalChunks) {
          return;
        }

        const range = chunkRanges[chunkIndex];
        setProgressTarget(calculateProgressPercent(completedChunks, totalChunks));
        if (loadingText) {
          loadingText.textContent = `Checking Inspector Gadget: chunk ${chunkIndex + 1}/${totalChunks}...`;
        }

        const partialReports = await submitGroupingRangeWithAdaptiveSplit({
          files,
          accessToken,
          refreshToken: "",
          countryCode,
          getAccessToken: null,
          setAccessToken: null,
          start: range.start,
          end: range.end,
          chunkIndex,
          totalChunks,
          setProgressTarget,
          getProgressPercent: () => calculateProgressPercent(completedChunks, totalChunks),
        });

        for (const partial of partialReports) {
          mergeGroupingReports(aggregated, partial);
        }

        completedChunks += 1;
        setProgressTarget(calculateProgressPercent(completedChunks, totalChunks));
      }
    };

    await Promise.all(Array.from({ length: parallelism }, () => worker()));

    await waitForDisplayedProgress(100);
    stopProgressTicker();

    normalizeGroupingAggregate(aggregated);

    // Recalculate percentages client-side after merge
    const pct = (n) => aggregated.total === 0 ? "0.0%" : `${(n / aggregated.total * 100).toFixed(1)}%`;
    aggregated.grouped_pct   = pct(aggregated.grouped);
    aggregated.ungrouped_pct = pct(aggregated.ungrouped);
    aggregated.not_found_pct = pct(aggregated.not_found);
    aggregated.errors_pct    = pct(aggregated.errors);
    aggregated.pending_visibility_pct = pct(aggregated.pending_visibility);

    // Sort all_results by series → season → episode
    aggregated.all_results.sort((a, b) =>
      (a.series_name || "").localeCompare(b.series_name || "") ||
      (a.season_number || 0) - (b.season_number || 0) ||
      (a.episode_number || 0) - (b.episode_number || 0)
    );

    renderGroupingResults(aggregated);
    if (clearBtn) clearBtn.style.display = "";
  } catch (err) {
    firstError = err && err.message ? err.message : "Unexpected error.";
    showGroupingError(firstError);
  } finally {
    stopProgressTicker();
    loading.classList.add("hidden");
    if (loadingText) loadingText.textContent = "Progress:";
    runBtn.disabled = false;
  }
}

async function handleGroupingCheckS3() {
  const token = getCurrentRefreshToken();
  if (!token) {
    showGroupingError("Please enter an Inspector Gadget refresh token.");
    return;
  }

  if (!window.SELECTED_PROVIDER_ID) {
    showGroupingError("Please select a provider first.");
    return;
  }

  const bucket = (document.getElementById("s3Bucket")?.value || "").trim();
  const convertedPrefix = (document.getElementById("s3ConvertedPrefix")?.value || "").trim();
  const startDate = (document.getElementById("dateRangeStart")?.value || "").trim();
  const endDate = (document.getElementById("dateRangeEnd")?.value || "").trim();
  const providerConfig = window.PROVIDER_S3_CONFIG[window.SELECTED_PROVIDER_ID] || {};

  if (!bucket) {
    showGroupingError("S3 bucket is empty. Select provider/channel in S3 mode first.");
    return;
  }
  if (!convertedPrefix) {
    showGroupingError("Converted S3 prefix is empty. Select a channel or provider with converted prefix first.");
    return;
  }
  if (!startDate || !endDate) {
    showGroupingError("Please select S3 start/end date before running S3 grouping check.");
    return;
  }

  const countryCode = String(window.SELECTED_COUNTRY_CODE || "FI").toUpperCase();
  const loading = document.getElementById("groupingLoading");
  const loadingText = loading ? loading.querySelector("p") : null;
  const resultsDiv = document.getElementById("groupingResults");
  const clearBtn = document.getElementById("clearGroupingResults");
  const runBtn = document.getElementById("runGroupingCheck");

  resultsDiv.innerHTML = "";
  if (clearBtn) clearBtn.style.display = "none";
  if (loading) loading.classList.remove("hidden");
  if (loadingText) loadingText.textContent = "Loading converted records from S3...";
  if (runBtn) runBtn.disabled = true;

  try {
    const accessToken = await resolveGroupingAccessToken(token);

    const resp = await fetch("/api/grouping-check/s3", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        access_token: accessToken,
        country_code: countryCode,
        provider_id: window.SELECTED_PROVIDER_ID,
        bucket,
        converted_prefix: convertedPrefix,
        start_date: startDate,
        end_date: endDate,
        aws_profile: providerConfig.aws_profile || "",
      }),
    });

    const data = await parseApiResponse(resp);
    if (!resp.ok) {
      throw new Error(data.error || "S3 grouping check failed.");
    }

    if (loadingText) loadingText.textContent = "Progress: 100%";
    renderGroupingResults(data);
    if (clearBtn) clearBtn.style.display = "";
  } catch (err) {
    showGroupingError(err.message || "Unexpected error while running S3 grouping check.");
  } finally {
    if (loading) loading.classList.add("hidden");
    if (loadingText) loadingText.textContent = "Progress:";
    if (runBtn) runBtn.disabled = false;
  }
}

function mergeGroupingReports(aggregated, partial) {
  if (!aggregated.checked_at) aggregated.checked_at = partial.checked_at;
  // Merge raw rows; totals and grouped/ungrouped/not_found/errors are recalculated after normalization.
  aggregated.all_results.push(...(partial.all_results || []));
}

function normalizeGroupingAggregate(report) {
  const rows = Array.isArray(report.all_results) ? report.all_results : [];
  const byGuid = new Map();

  const rank = {
    grouped: 4,
    ungrouped: 3,
    not_found: 2,
    error: 1,
  };

  for (const row of rows) {
    const guid = String((row && row.guid) || "").trim();
    if (!guid) {
      continue;
    }

    const current = byGuid.get(guid);
    if (!current) {
      byGuid.set(guid, { ...row });
      continue;
    }

    const nextStatus = String(row.status || "");
    const currentStatus = String(current.status || "");
    if ((rank[nextStatus] || 0) > (rank[currentStatus] || 0)) {
      current.status = nextStatus;
    }

    current.series_name = current.series_name || row.series_name;
    current.series_guid = current.series_guid || row.series_guid;
    current.media_id = current.media_id || row.media_id;
    current.series_id = current.series_id || row.series_id;
    current.season_id = current.season_id || row.season_id;
    current.source_file = current.source_file || row.source_file;
    current.via_fallback = current.via_fallback || !!row.via_fallback;

    if (current.season_number == null && row.season_number != null) {
      current.season_number = row.season_number;
    }
    if (current.episode_number == null && row.episode_number != null) {
      current.episode_number = row.episode_number;
    }
  }

  const dedupedRows = Array.from(byGuid.values());
  dedupedRows.sort((a, b) =>
    (a.series_name || "").localeCompare(b.series_name || "") ||
    (a.season_number || 0) - (b.season_number || 0) ||
    (a.episode_number || 0) - (b.episode_number || 0)
  );

  const ungroupedBySeries = {};
  let grouped = 0;
  let ungrouped = 0;
  let notFound = 0;
  let errors = 0;
  let pendingVisibility = 0;

  for (const row of dedupedRows) {
    const status = String(row.status || "");
    if (status === "grouped") {
      grouped += 1;
      if (row.via_fallback) {
        pendingVisibility += 1;
      }
      continue;
    }
    if (status === "ungrouped") {
      ungrouped += 1;
      const seriesKey = row.series_name || row.series_guid || "<unknown>";
      if (!ungroupedBySeries[seriesKey]) {
        ungroupedBySeries[seriesKey] = [];
      }
      ungroupedBySeries[seriesKey].push(row);
      continue;
    }
    if (status === "not_found") {
      notFound += 1;
      continue;
    }
    errors += 1;
  }

  for (const key of Object.keys(ungroupedBySeries)) {
    ungroupedBySeries[key].sort((a, b) =>
      (a.season_number || 0) - (b.season_number || 0) ||
      (a.episode_number || 0) - (b.episode_number || 0)
    );
  }

  report.total = dedupedRows.length;
  report.grouped = grouped;
  report.ungrouped = ungrouped;
  report.not_found = notFound;
  report.errors = errors;
  report.pending_visibility = pendingVisibility;
  report.ungrouped_by_series = ungroupedBySeries;
  report.all_results = dedupedRows;
}

function clearGroupingResults() {
  const resultsDiv = document.getElementById("groupingResults");
  const clearBtn = document.getElementById("clearGroupingResults");
  if (resultsDiv) resultsDiv.innerHTML = "";
  if (clearBtn) clearBtn.style.display = "none";
}

function showGroupingError(msg) {
  const resultsDiv = document.getElementById("groupingResults");
  resultsDiv.innerHTML = `<div class="gc-error-inline">${escapeHtml(msg)}</div>`;
}

function renderGroupingRows(rows) {
  return rows.map((row) => {
    const pendingVisibility = row.status === "grouped" && row.via_fallback;
    const statusHtml =
      pendingVisibility ? "⚠️ grouped (not visible in IG search/channel yet)" :
      row.status === "grouped" ? "✅ grouped" :
      row.status === "ungrouped" ? "❌ ungrouped" :
      row.status === "not_found" ? "❓ not found" : "⚠️ error";
    const errorText = row.status === "error" && row.error
      ? `<div class="gc-row-error" title="${escapeHtml(row.error)}">${escapeHtml(row.error)}</div>`
      : "";
    return `
      <tr${pendingVisibility ? ' class="gc-row-pending-visibility"' : ""}>
        <td>${escapeHtml(row.series_name || "—")}</td>
        <td>${row.season_number != null ? row.season_number : "—"}</td>
        <td>${row.episode_number != null ? row.episode_number : "—"}</td>
        <td class="mono">${escapeHtml(row.guid)}</td>
        <td class="mono">${escapeHtml(row.series_id || "—")}</td>
        <td class="mono">${escapeHtml(row.media_id || "—")}</td>
        <td>${statusHtml}${errorText}</td>
      </tr>
    `;
  }).join("");
}

function renderGroupingStatusSection({ icon, label, count, pct, rows, toneClass, open = false }) {
  const sectionRows = renderGroupingRows(rows);
  const openAttr = open ? " open" : "";
  return `
    <details class="gc-status-section ${toneClass}"${openAttr}>
      <summary class="gc-status-summary">
        <span class="gc-status-summary-main">
          <span class="gc-stat-icon">${icon}</span>
          <span class="gc-status-summary-copy">
            <span class="gc-status-summary-label">${escapeHtml(label)}</span>
            <span class="gc-status-summary-meta">${count} episode${count === 1 ? "" : "s"} • ${escapeHtml(pct)}</span>
          </span>
        </span>
      </summary>
      <div class="gc-status-body">
        ${rows.length === 0 ? `<div class="gc-empty-state">No ${escapeHtml(label.toLowerCase())} episodes.</div>` : `
          <table class="gc-ep-table">
            <thead>
              <tr><th>Series</th><th>S</th><th>E</th><th>GUID</th><th>Series ID</th><th>Media ID</th><th>Status</th></tr>
            </thead>
            <tbody>${sectionRows}</tbody>
          </table>
        `}
      </div>
    </details>
  `;
}

function renderGroupingResults(report) {
  const resultsDiv = document.getElementById("groupingResults");
  let html = "";

  // ── Stat cards ────────────────────────────────────────────────────────
  html += `
    <div class="gc-inline-stats">
      <div class="gc-inline-stat gc-stat-grouped">
        <div class="gc-stat-icon">✅</div>
        <div class="gc-stat-count">${report.grouped}</div>
        <div class="gc-stat-label">Grouped</div>
        <div class="gc-stat-pct">${report.grouped_pct}</div>
      </div>
      <div class="gc-inline-stat gc-stat-ungrouped">
        <div class="gc-stat-icon">❌</div>
        <div class="gc-stat-count">${report.ungrouped}</div>
        <div class="gc-stat-label">Ungrouped</div>
        <div class="gc-stat-pct">${report.ungrouped_pct}</div>
      </div>
      <div class="gc-inline-stat gc-stat-notfound">
        <div class="gc-stat-icon">❓</div>
        <div class="gc-stat-count">${report.not_found}</div>
        <div class="gc-stat-label">Not found</div>
        <div class="gc-stat-pct">${report.not_found_pct}</div>
      </div>
      <div class="gc-inline-stat gc-stat-errors">
        <div class="gc-stat-icon">⚠️</div>
        <div class="gc-stat-count">${report.errors}</div>
        <div class="gc-stat-label">Errors</div>
        <div class="gc-stat-pct">${report.errors_pct}</div>
      </div>
      <div class="gc-inline-stat gc-stat-pending-visibility">
        <div class="gc-stat-icon">🔎</div>
        <div class="gc-stat-count">${report.pending_visibility || 0}</div>
        <div class="gc-stat-label">Grouped, pending IG visibility</div>
        <div class="gc-stat-pct">${report.pending_visibility_pct || "0.0%"}</div>
      </div>
    </div>
    <div class="gc-inline-meta">
      Checked at ${escapeHtml(report.checked_at)} &nbsp;|&nbsp; ${report.total} episodes
      &nbsp;
      <button type="button" class="btn btn-secondary" onclick="downloadGroupingReport()" style="font-size:0.8rem;padding:0.25rem 0.6rem;">
        ⬇ Download JSON
      </button>
    </div>
  `;

  window.LAST_GROUPING_REPORT = report;

  const groupedRows = (report.all_results || []).filter((row) => row.status === "grouped");
  const ungroupedRows = (report.all_results || []).filter((row) => row.status === "ungrouped");
  const notFoundRows = (report.all_results || []).filter((row) => row.status === "not_found");
  const errorRows = (report.all_results || []).filter((row) => row.status === "error");

  const pendingVisibilityRows = groupedRows.filter((row) => row.via_fallback);

  html += `<div class="gc-status-sections">`;
  html += renderGroupingStatusSection({
    icon: "✅",
    label: "Grouped",
    count: report.grouped,
    pct: report.grouped_pct,
    rows: groupedRows,
    toneClass: "gc-stat-grouped",
  });
  if (pendingVisibilityRows.length > 0) {
    html += renderGroupingStatusSection({
      icon: "🔎",
      label: "Grouped, pending IG visibility (metadata is grouped, but not confirmed visible in IG search/channel listing)",
      count: pendingVisibilityRows.length,
      pct: report.pending_visibility_pct || "0.0%",
      rows: pendingVisibilityRows,
      toneClass: "gc-stat-pending-visibility",
      open: true,
    });
  }
  html += renderGroupingStatusSection({
    icon: "❌",
    label: "Ungrouped",
    count: report.ungrouped,
    pct: report.ungrouped_pct,
    rows: ungroupedRows,
    toneClass: "gc-stat-ungrouped",
  });
  html += renderGroupingStatusSection({
    icon: "❓",
    label: "Not found",
    count: report.not_found,
    pct: report.not_found_pct,
    rows: notFoundRows,
    toneClass: "gc-stat-notfound",
  });
  html += renderGroupingStatusSection({
    icon: "⚠️",
    label: "Errors",
    count: report.errors,
    pct: report.errors_pct,
    rows: errorRows,
    toneClass: "gc-stat-errors",
  });
  html += `</div>`;

  const allRows = renderGroupingRows(report.all_results || []);

  html += `
    <details style="margin-top:1.5rem;">
      <summary style="cursor:pointer;font-weight:600;font-size:1rem;margin-bottom:0.5rem;">
        📋 All episodes (${report.total})
      </summary>
      <table class="gc-ep-table" style="margin-top:0.5rem;">
        <thead>
          <tr><th>Series</th><th>S</th><th>E</th><th>GUID</th><th>Series ID</th><th>Media ID</th><th>Status</th></tr>
        </thead>
        <tbody>${allRows}</tbody>
      </table>
    </details>
  `;

  resultsDiv.innerHTML = html;
}

function downloadGroupingReport() {
  const report = window.LAST_GROUPING_REPORT;
  if (!report) return;
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "grouping_check_report.json";
  a.click();
  URL.revokeObjectURL(url);
}

function setupIngestionTests() {
  const runBtn = document.getElementById("runIngestionTests");
  const clearBtn = document.getElementById("clearIngestionTestResults");
  if (!runBtn) return;
  runBtn.addEventListener("click", handleRunIngestionTests);
  if (clearBtn) clearBtn.addEventListener("click", clearIngestionTestResults);
}

async function handleRunIngestionTests() {
  const originalInput = document.getElementById("originalFiles");
  const convertedInput = document.getElementById("convertedFiles");
  const baselineInput = document.getElementById("baselineConvertedFiles");
  const testsConfigInput = document.getElementById("ingestionTestsConfigPath");

  const originalFiles = originalInput ? Array.from(originalInput.files || []) : [];
  const convertedFiles = convertedInput ? Array.from(convertedInput.files || []) : [];
  const baselineFiles = baselineInput ? Array.from(baselineInput.files || []) : [];
  const testsConfigPath = testsConfigInput
    ? String(testsConfigInput.value || "").trim()
    : "configs/providers/nrk-tv/nrk-tv.repeating_ingestion.tests.yaml";

  if (originalFiles.length === 0) {
    showIngestionTestError("Please upload original metadata files in the section above first.");
    return;
  }
  if (convertedFiles.length === 0) {
    showIngestionTestError("Please upload converted metadata files in the section above first.");
    return;
  }

  const loading = document.getElementById("ingestionLoading");
  const loadingText = loading ? loading.querySelector("p") : null;
  const runBtn = document.getElementById("runIngestionTests");
  const clearBtn = document.getElementById("clearIngestionTestResults");
  const resultsDiv = document.getElementById("ingestionResults");

  if (resultsDiv) resultsDiv.innerHTML = "";
  if (clearBtn) clearBtn.style.display = "none";
  if (loading) loading.classList.remove("hidden");
  if (runBtn) runBtn.disabled = true;
  if (loadingText) loadingText.textContent = "Running ingestion tests...";

  try {
    const form = new FormData();
    form.append("tests_config", testsConfigPath);
    for (const file of originalFiles) {
      form.append("original_files", file);
    }
    for (const file of convertedFiles) {
      form.append("converted_files", file);
    }
    for (const file of baselineFiles) {
      form.append("baseline_converted_files", file);
    }

    const resp = await fetch("/api/ingestion-tests", { method: "POST", body: form });
    const data = await parseApiResponse(resp);

    if (!resp.ok) {
      throw new Error(data.error || "Failed to run ingestion tests");
    }

    renderIngestionTestResults(data);
    if (clearBtn) clearBtn.style.display = "";
  } catch (err) {
    showIngestionTestError(`Error: ${err.message}`);
  } finally {
    if (loading) loading.classList.add("hidden");
    if (runBtn) runBtn.disabled = false;
  }
}

function clearIngestionTestResults() {
  const resultsDiv = document.getElementById("ingestionResults");
  const clearBtn = document.getElementById("clearIngestionTestResults");
  if (resultsDiv) resultsDiv.innerHTML = "";
  if (clearBtn) clearBtn.style.display = "none";
}

function showIngestionTestError(message) {
  const resultsDiv = document.getElementById("ingestionResults");
  if (!resultsDiv) return;
  resultsDiv.innerHTML = `<div class="gc-error-inline">${escapeHtml(message)}</div>`;
}

function renderIngestionTestResults(report) {
  const resultsDiv = document.getElementById("ingestionResults");
  if (!resultsDiv) return;

  const summary = report.summary || {};
  const inputs = report.inputs || {};
  const tests = report.test_results || [];

  const rows = tests
    .map((test) => {
      const assertions = Array.isArray(test.assertions) ? test.assertions : [];
      const passed = assertions.filter((a) => a.status === "passed").length;
      const failed = assertions.filter((a) => a.status === "failed").length;
      const skipped = assertions.filter((a) => a.status === "skipped").length;
      const badgeClass =
        test.status === "passed"
          ? "sev-low"
          : test.status === "skipped"
            ? "sev-medium"
            : "sev-high";

      return `
        <tr>
          <td class="mono">${escapeHtml(test.test_case_id || "")}</td>
          <td>${escapeHtml(test.test_name || "")}</td>
          <td><span class="severity-badge ${badgeClass}">${escapeHtml(test.status || "")}</span></td>
          <td>${passed}</td>
          <td>${failed}</td>
          <td>${skipped}</td>
        </tr>
      `;
    })
    .join("");

  const generatedAt = escapeHtml(String(report.generated_at || ""));
  const suiteId = escapeHtml(String((report.suite || {}).suite_id || ""));
  const providerId = escapeHtml(String((report.suite || {}).provider_id || ""));

  resultsDiv.innerHTML = `
    <div class="gc-inline-stats">
      <div class="gc-inline-stat gc-stat-grouped">
        <div class="gc-stat-count">${summary.passed || 0}</div>
        <div class="gc-stat-label">Passed</div>
      </div>
      <div class="gc-inline-stat gc-stat-ungrouped">
        <div class="gc-stat-count">${summary.failed || 0}</div>
        <div class="gc-stat-label">Failed</div>
      </div>
      <div class="gc-inline-stat gc-stat-notfound">
        <div class="gc-stat-count">${summary.skipped || 0}</div>
        <div class="gc-stat-label">Skipped</div>
      </div>
      <div class="gc-inline-stat gc-stat-errors">
        <div class="gc-stat-count">${summary.total_test_cases || 0}</div>
        <div class="gc-stat-label">Total Tests</div>
      </div>
    </div>
    <div class="gc-inline-meta">
      Suite ${suiteId} | Provider ${providerId} | Generated ${generatedAt}<br/>
      Loaded records: original ${inputs.original_records_loaded || 0}, converted ${inputs.converted_records_loaded || 0}, baseline ${inputs.baseline_converted_records_loaded || 0}
    </div>
    <details style="margin-top:1rem;" open>
      <summary style="cursor:pointer;font-weight:600;font-size:1rem;">Test case results</summary>
      <table class="gc-ep-table" style="margin-top:0.75rem;">
        <thead>
          <tr>
            <th>Test case</th>
            <th>Name</th>
            <th>Status</th>
            <th>Passed</th>
            <th>Failed</th>
            <th>Skipped</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </details>
  `;

  window.LAST_INGESTION_TEST_REPORT = report;
}
