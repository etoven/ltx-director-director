# Werecreature Effects Library

100 reusable effect entries, 100 effect thumbnails, 24 animal anatomy presets, 17 short source excerpts, 17 playable movie soundtrack WAV samples and 17 verified footage-source records. Version 1.2.0, prepared 2026-10-19.

## Start here

1. Extract the ZIP completely and open `index.html` in a browser. It works offline; source links need internet access.
2. Search a body part or animal, select MiniMax or Gemini wording, and copy one segment.
3. Attach your source shot and, when useful, the selected local reference excerpt. Choose the side and desired endpoint before applying it.
4. For agent-assisted import, give the agent `agent.md`. For programmatic use, ingest `effects.json` or `effects.jsonl`; keep media paths relative to this folder.
5. Open **Sound samples** to preview and download WAVs. Filter by film or search by scene/body part. Source samples also appear beside linked effects and on footage cards. Playback is manual and stops when another sample starts or you change sections.

## Included

| File or folder | Purpose |
| --- | --- |
| `index.html` | Searchable illustrated table, model wording toggle, copy buttons, local video and WAV playback/download |
| `effects.json` / `effects.jsonl` | Canonical 100 effect records with prompts, anatomy constraints and media/source mappings |
| `species.json` | 24 endpoint presets, including wolf, cheetah, bear, panther, horse, cow, bull, birds and cats |
| `thumbnails/` | One labeled thumbnail for each effect |
| `illustrations/` | 40 generated animal/body-region examples and the three original concept atlases, exported as JPEG |
| `reference_clips/` | Short actual movie/trailer excerpts and their unaltered source-frame posters |
| `sources/clips.json` | Exact excerpt times, observed content, evidence type and limitations |
| `audio_samples/source/` | 17 audio-only extractions of the existing short movie/trailer clips; mixed source soundtracks |
| `sources/audio.json` / `sources/audio_index.md` | Sample IDs, paths, formats, levels, provenance and related effects / portable WAV index |
| `audio.schema.json` | Library-defined schema for audio sample metadata |
| `sources/footage.json` | Source links, dates, film years and local-clip availability |
| `sources/documentation.json` | Official model guidance, production sources and selected anatomy references |
| `prompts/` | Plain-text MiniMax and Gemini snippets, plus optional H3 reference-insertion lines |
| `agent.md` | Import and selection instructions for a coding/prompt agent |
| `import_library.py` | Offline validation, search and single-segment export; Python standard library only |
| `checksums.sha256` | File integrity inventory |

## Scope and evidence

The main film cutoff is 2021 onward. Underworld (2003) and Van Helsing (2004) are explicitly requested exceptions. Werewolf by Night (2022) is clearly marked as a television special. Release-year differences such as Viking Wolf (2022 / Netflix 2023) are retained in the source records.

Actual packaged excerpts come from Wolf Man (2025), Van Helsing, Bhediya, Werewolves (2024), The Animal Kingdom and Viking Wolf. Underworld is a verified linked reference; its video is not included locally. Other source records may also be links only. Full movies and full trailers are not packaged.

Every effect has a thumbnail. Generated thumbnails illustrate an endpoint or a body region and may be reused across related effects; they are not exact per-effect animation frames. Sound thumbnails are conceptual graphics, not measured waveforms. Movie stills appear separately as excerpt posters. The written anatomy constraints take precedence over imperfections in generated illustrations.

The cheetah, bear, panther, horse, cow, bull, bird and cat abstractions are author-created endpoint presets. They do not imply that all those species appear in the listed movies. A trailer glimpse, endpoint pose and continuous morph are different evidence types; the clip log states which one is supplied.

The 16 Sound entries are written sound-design recipes. Separate isolated recordings for those recipes are not supplied. All included WAV samples are actual audio extracted from the packaged source footage, in stereo 48 kHz, 16-bit PCM format.

The 17 source WAVs are audio-only extracts of the packaged movie/trailer excerpts. They retain the mixed soundtrack and can contain dialogue or music; they are not isolated foley. Use video-only reference unless you deliberately want its audio. Source excerpts, source audio and source frames remain copyrighted by their respective owners; this package does not grant a new media reuse license.

Previewing or downloading audio does not upload it to a model. Keep sound optional, attach a chosen file through a supported audio-input workflow, and set timing/intensity for the target shot. See `audio_samples/README.md` for details.

When updating an earlier import, remove its standalone synthesized demo recordings and their asset mappings. Preserve user-owned recordings. Effect IDs and actual source-media IDs remain stable.

These are prompt fragments to insert into an existing shot, not complete six-section H3 prompts. The import schema belongs to this library, not to MiniMax or Google. No live Gemini or MiniMax render was run, and no quality, speed or VRAM benchmark is claimed.

## Useful commands

```bash
python3 import_library.py --validate
python3 import_library.py --list hoof
python3 import_library.py --id FX021 --model minimax --species panther --side left --stage intermediate
python3 import_library.py --id FX010 --reference-clip CLIP008 --model minimax --bundle > selected_effect.json
python3 import_library.py --id FX075 --bundle
python3 import_library.py --id FX005 --with-sound --bundle
python3 import_library.py --id FX010 --reference-clip CLIP008 --with-sound --source-audio --bundle
```

For visual effects, `--with-sound` adds only the optional written sound instruction. Mixed source audio requires explicit `--source-audio`, `--reference-clip` and `--bundle`. Sound recipe entries have no standalone recordings attached. Bundles report media as not uploaded. These flags do not add new model parameters or call an API.

Use one mechanic per short edit pass when possible. Add a species preset only for the selected visible region. Stop at the requested intermediate state instead of completing the entire transformation.
