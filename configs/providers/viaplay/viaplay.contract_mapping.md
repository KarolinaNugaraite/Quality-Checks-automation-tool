# Viaplay Provider Mapping to Internal Contract

This mapping is based on Viaplay JSON-LD source objects (example: TVEpisode payloads) and the internal metadata contract in tv.metadata-contract-master.

## Developer Handoff: Where These Rules Come From

- Viaplay provider rule YAMLs in this repo are local overlays, not the master contract itself.
- Primary file created from this mapping work: configs/providers/viaplay/viaplay.rules.yaml
- EPG-specific overlay: configs/providers/viaplay/viaplay-epg.rules.yaml
- Contract source of truth lives outside this repo in: tv.metadata-contract-master
- Contract schema parts referenced while deriving rules:
  - tv.metadata-contract-master/src/schema/parts/content.json
  - tv.metadata-contract-master/src/schema/parts/schedule.json

## Shared Contract vs Provider Rules

- Shared contract (tv.metadata-contract-master): canonical metadata structure and required objects/fields.
- Provider rules (this repo): provider-specific key candidates, tolerances, and check tuning for actual source payload shape.
- Result: provider YAMLs should be treated as an implementation overlay on top of the shared contract, not as a replacement for it.

## How to Recreate or Update Viaplay Rules

1. Read the shared contract parts in tv.metadata-contract-master/src/schema/parts.
2. Inspect Viaplay source payloads and list real source paths for each required/recommended contract field.
3. Encode those source paths under id_keys, type_keys, and candidate_keys in configs/providers/viaplay/viaplay.rules.yaml.
4. Keep provider-only constraints in provider YAML (example: image ratio expectations, schedule-only checks, allowed channel IDs).
5. If contract schema changes upstream, update this mapping document first, then update provider YAML rules.

## Scope

- Source: Viaplay JSON-LD records in sample_data/viaplay/raw
- Converted metadata input: JSON files in sample_data/viaplay/converted (provided by upstream pipeline/S3)
- Rules config: configs/providers/viaplay/viaplay.rules.yaml

## Required Contract Fields (Relevant for Source-Level Validation)

1. Content object (parts/content.json)
- required: contentId
- required: titles (minItems: 1)

2. Title object
- required: type
- required: value

3. Description object (when present)
- required: type
- required: value

4. Image object (when present)
- required: type
- required: url
- required: aspectRatio

5. Deeplink object (when present)
- required: platforms
- required: link
- required: sites

## Source to Normalized Mapping

1. Identity and type
- source guid -> normalized contentId
- source guid -> normalized originalContentId
- source @type/type -> normalized type

2. Titles and descriptions
- source name -> normalized title
- source name -> normalized titles[{type: FULL, value: name}]
- source name/original -> normalized titles[{type: ORIGINAL, value: name/original}] when different
- source description -> normalized description
- source description -> normalized descriptions[{type: SHORT, value: description}] when present

3. Production year and rating
- source releasedEvent.startDate -> normalized productionYear (year extracted)
- source contentRating.ratingValue -> normalized ageRating

4. Genres
- source genre[] -> normalized genres[]

5. Images
- source image[] -> normalized images[]
- source image.urlTemplate -> normalized images[].url
- source image.width / image.height -> normalized images[].ratio and images[].aspectRatio
- source image.name + source type -> normalized images[].type (mapped to contract-like enum family)

6. Deeplinks
- source potentialAction[].target[].urlTemplate -> normalized deeplink (single preferred URL)
- source potentialAction[] -> normalized deeplinks[] objects with:
  - platforms (mapped from actionPlatform)
  - link (urlTemplate)
  - sites ([{whiteLabelBrand: TELIA, country: metadataOriginCountry uppercased}])

## Current Validation Intent in Provider Rules

- Primary checks for source completeness:
  - id presence
  - title presence
  - description presence
  - image presence/ratios/types
  - genre presence
  - production year
  - duration
  - age rating
  - deeplink presence

- Provider-specific tuning:
  - required_image_ratios currently set to ["16:9"] for Viaplay sample shape.
