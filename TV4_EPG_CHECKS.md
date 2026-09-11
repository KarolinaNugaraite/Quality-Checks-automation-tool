# TV4 EPG Checks

This document describes the checks performed for the TV4 EPG provider and the fields shown in the **Missing Fields by Channel** analytics table.

## Source Shape

The TV4 adapter reads EPG data from:

```text
data[].channel
 data[].broadcasts[]
```

If the payload is wrapped by OpenSearch, the adapter first reads the `_source` object.

Each broadcast is normalized before validation:

- `program_external_reference` becomes `contentId`.
- `tx_external_reference` is used as the primary broadcast ID.
- `public.start` and `public.end` become the display start and end times.
- `planned.start` and `planned.end` are used as a fallback when public times are absent.
- `live` is derived from the broadcast `type` or `status`.
- `product_code` is read directly from the broadcast.
- Channel and file names are used to identify the source location in findings.

## Required Broadcast Fields

The following fields are required for every normalized TV4 broadcast:

- `contentId`
- `broadcastId`
- `channel_id`
- `displayTime.start`
- `displayTime.end`
- `product_code`

A missing required field produces a structural validation finding.

### Why these fields?

These are the minimum fields needed to identify a TV4 broadcast, identify its
channel, connect it to content, place it on the schedule, and identify the
product. They are explicitly listed in the provider configuration under
`required_broadcast_fields`.

The other analytics fields are not automatically structural requirements. They
fall into one of these categories:

- **Quality-checked fields:** titles, descriptions, genres, images, age ratings,
	language codes, country codes, and timing are checked when their configured
	source values are available.
- **Source-dependent fields:** TV4 does not always provide related program data
	such as descriptions, genres, images, age ratings, episode numbers, series,
	or seasons.
- **Derived fields:** values such as `live` and channel identity are created by
	the TV4 adapter from other source values.
- **Not currently structural requirements:** Episode Number, Series, Season,
	and Channel External Name currently use broad schema validation rather than
	dedicated field-specific required checks.

If another field must be mandatory for the TV4 integration, add it to
`required_broadcast_fields` in
`configs/providers/tv4-media/tv4-media-epg.rules.yaml` and ensure the adapter
maps it from the source payload. That changes the field from a quality check to
a structural validation requirement.

## Analytics Fields

The analytics table displays the number of records with a matching finding. A value of `0` means that no matching finding was detected. Values greater than `0` show the number of affected records.

### Content ID

Checks that:

- every broadcast has a `contentId`; and
- the broadcast `contentId` resolves to a content item when content references are available.

### Product Code

Checks that the required `product_code` value is present on each broadcast.

### Title ID

The current TV4 configuration does not provide a dedicated title-ID check. This column is currently mapped to the general `MISSING_TITLE` finding and should therefore be interpreted as title presence rather than a separate title-ID validation.

### Titles

Checks that a title can be found using the configured TV4 title mappings and that title language codes are valid.

TV4 title values may come from the broadcast itself or from the related program metadata when that data is available.

### Descriptions

Checks that descriptions are available and usable:

- a description is present;
- the description is not too short; and
- the description is not a configured placeholder such as `n/a`, `tbd`, or `description`.

TV4 descriptions are read from related program metadata when available.

### Genres

Checks that:

- a genre is present; and
- the genre can be mapped to the configured genre vocabulary.

TV4 genre data is expected from related program metadata when available.

### Images

Checks image quality and image metadata, including:

- a required image is present;
- a 16:9 image is available;
- the image type is recognized;
- the image URL is valid; and
- explicit image dimensions are valid when supplied.

TV4 image data is expected from related program metadata when available.

### Age Ratings

Checks that an age-rating value is present. TV4 age-rating data is expected from related program metadata when available.

### Episode Number

This field is currently associated with the general `SCHEMA_ERROR` category. It does not yet have an isolated TV4 episode-number error code, so the analytics value should be interpreted as schema-related findings associated with episode data.

### Series

This field is currently associated with the general `SCHEMA_ERROR` category. It does not yet have an isolated TV4 series validation.

### Season

This field is currently associated with the general `SCHEMA_ERROR` category. It does not yet have an isolated TV4 season validation.

### Display Start Time

Checks that the normalized broadcast start time is present and valid. TV4 uses:

1. `public.start`
2. `planned.start` as a fallback

A broadcast with an invalid or non-positive time range produces a date validation finding.

### Display End Time

Checks that the normalized broadcast end time is present and valid. TV4 uses:

1. `public.end`
2. `planned.end` as a fallback

The end time must be later than the start time.

### Channel External Name

The adapter derives the channel identity from the available channel fields, including:

- `channelId`
- `channel.id`
- `channel.name`
- `channel.display_name`

The channel identity is structurally required as `channel_id` and missing values
produce `MISSING_CHANNEL_ID`. Other channel-name schema problems may still use
the broader `SCHEMA_ERROR` category.

### Live

Checks that broadcast flags are not contradictory. A broadcast must not have more than one of the following flags active at the same time:

- `rerun`
- `live`
- `premiere`

For TV4, the normalized `live` value is derived from the broadcast `type` or `status` when the text contains `live`.

### Broadcast Rights

Checks that publishing or broadcast-rights information is present.

For TV4, rights are partially derived from broadcast data such as:

- `embargo.hide`
- broadcast `type`
- broadcast `status`

### Targets

Checks that publishing information contains at least one target.

TV4 currently does not provide a native `targets` field. This is documented in the provider configuration as `MISSING_FROM_SOURCE`. Any non-zero value in this column therefore represents the expected absence of publishing targets unless TV4 source data is extended in the future.

### Broadcast ID

Checks that a real broadcast identifier is present and that broadcast
identifiers are unique within the checked channel and schedule data.

The adapter uses the following identifiers in priority order:

1. `tx_external_reference`
2. `txb_external_reference`
3. `broadcast_external_reference`
4. `broadcastId`
5. `id`

If none of these source identifiers is present, the adapter may create an
internal fallback for processing, but it does not expose that fallback as the
normalized `broadcastId`. The structural check therefore reports
`MISSING_BROADCAST_ID`.

### Broadcast Timing

Checks the schedule timeline per channel for:

- gaps between consecutive broadcasts;
- overlapping broadcast start times;
- overlapping broadcast end times; and
- schedules longer than 24 hours.

TV4 uses the normalized `displayTime.start` and `displayTime.end` values for these checks. The TV4 provider currently skips the generic envelope-level EPG date contract because its schedule window is defined per broadcast.

### Country Code

Checks that the country code is valid according to the accepted country-code mappings.

TV4 country values are read from:

1. `channel.country`
2. `country`
3. `metadataOriginCountry`

### Language Code

Checks that the language code is valid according to the accepted ISO language-code mappings.

TV4 language values are read from:

1. `channel.language`
2. `language`
3. `lang`

## Source Availability Notes

The following fields are not normally provided directly by the TV4 broadcast payload and depend on related program data:

- descriptions;
- genres;
- images;
- age ratings;
- episode number;
- series; and
- season.

If `related.programs` is absent or does not contain the matching `program_external_reference`, these fields may correctly produce missing-data findings.

## Interpretation of Analytics Counts

The analytics table groups configured error codes under readable field names. A single underlying error code may therefore contribute to more than one displayed field, and broad error categories such as `SCHEMA_ERROR` are not always field-specific.

For exact troubleshooting, use the detailed findings list, which includes the finding message, source file, and content or broadcast identifier.
