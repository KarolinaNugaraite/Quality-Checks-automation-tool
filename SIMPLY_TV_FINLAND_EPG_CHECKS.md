# Simply TV Finland EPG Checks

This document describes the Simply TV Finland EPG adapter and the checks shown in the analytics dashboard.

## Source Shape

Simply TV provides three related collections:

```text
channels[]
programs[]
listings[]
```

A listing connects the collections:

- `listings[].channel_id` joins to `channels[].id`.
- `listings[].program_id` joins to `programs[].id`.
- `listings[].id` identifies the scheduled broadcast.

The adapter groups listings by channel so schedule continuity checks can compare consecutive broadcasts on the same channel.

## Normalization

The adapter maps the Simply TV source into the common checker model:

- `programs[].id` becomes `contentId`.
- `listings[].id` becomes `broadcastId`.
- `listings[].channel_id` and `channels[].id` become `channelId`.
- `listings[].schedule.start_time` becomes `displayTime.start`.
- `listings[].schedule.end_time` becomes `displayTime.end`.
- Program titles, descriptions, genres, images, release year, series, season, and episode data are taken from the joined program.
- `listings[].qualifiers.live` becomes the normalized live flag.
- `channels[].language` and `channels[].country` become the normalized language and country codes.
- The scheduled `start_time` and `end_time` values are used for EPG continuity. The `accurate` times are not used for slot boundaries because their second-level adjustments can create false overlaps between adjacent scheduled programs.

## Required Broadcast Fields

Every normalized Simply TV listing must contain:

- `contentId`
- `broadcastId`
- `channel_id`
- `displayTime.start`
- `displayTime.end`

Simply TV does not provide `product_code`, so it is not part of this provider's structural contract.

## Checks

### Content ID

Checks that a listing has a program reference and that the joined program becomes a valid content ID.

### Titles

Checks that at least one program title is available and that title language codes are valid. Simply TV commonly provides Finnish and Swedish title variants.

### Descriptions

Checks that program descriptions are present, are not too short, and are not configured placeholder text.

### Genres

Checks that program genre values are present and can be mapped to the configured genre vocabulary. Simply TV supplies genre names such as `News`, `Documentary`, and `Drama`.

### Images

Checks that program images include a valid 16:9 image, a supported image type, a usable URL, and numeric dimensions when dimensions are supplied.

Simply TV's image source uses `kind` and `level`. The adapter maps `level=show` and `level=episode` to the common `content` image type and converts resolutions such as `1920x1080` into numeric width and height values.

### Age Ratings

Checks that a parental rating is present when the listing supplies `qualifiers.parental_rating`. A rating value of `0` is valid and represents an unrestricted rating; it must not be treated as missing.

### Episode Number, Series, and Season

The adapter maps:

- `programs[].attributes.episode.number` to episode number;
- `programs[].series_id` to series identity; and
- `programs[].attributes.episode.season` to season number.

These fields are currently represented in the analytics matrix through the checker's broader schema validation category. They are not yet separate field-specific error codes.

### Display Start and End Time

Checks that listing schedule times are present, parseable, and form a positive interval:

```text
schedule.start_time < schedule.end_time
```

### Channel External Name

Checks that the listing resolves to a channel identity from `channel_id` and that the channel mapping is valid. The displayed channel name comes from `channels[].name`.

### Live

Uses `qualifiers.live` as the normalized live value. Simply TV does not provide the TV4-style rerun/premiere flag combination, so the contradictory live-flag check is disabled for this provider.

### Broadcast ID

Checks that every listing has a broadcast identifier and that identifiers are unique within the channel schedule. The primary source is `listings[].id`; `broadcast_ids.event` is the fallback source identifier.

### Broadcast Timing

Checks the scheduled timeline per channel for:

- gaps between consecutive listings;
- overlapping listing start times;
- overlapping listing end times; and
- schedules exceeding 24 hours.

### Country Code

Checks the channel country code from `channels[].country`. The Finland sample uses `FI`.

### Language Code

Checks the channel language code from `channels[].language`. The sample uses `FI`; individual program titles and descriptions may also contain `SV`, `EN`, or other valid language codes.

## Intentionally Disabled Checks

The following checks are not applicable to the supplied Simply TV EPG shape:

- envelope-level required fields;
- publishing rights and publishing targets;
- TV4-style rerun/live/premiere contradiction checks;
- VOD publishing structures; and
- the generic episode-count outlier check.

Simply TV listings contain broadcast qualifiers and parental ratings, but they do not contain the `publishingInfo.targets` structure used by VOD metadata.
