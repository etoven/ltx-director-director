# Aurora a171: Standard timeline conditioning

The remote worker merged timeline conditioning with Reference-panel inputs and then applied an opening/final-image policy to the merged inventory. A timed image checkpoint inside group 2 therefore raised the reported FL2VA error even with no Reference-panel inputs. Video continuation and its audio cannot trigger that particular check. The failed job request is no longer available locally, so its precise image inventory could not be recovered.

Standard now consumes only visual conditioning and excludes Reference-panel media and reference audio at the worker boundary as well as in the UI. Timed images use the native visual encoder and existing packed conditioning lane, with authored rotary time coordinates instead of being collapsed to Start/End. Reference mode retains its own reference lane. Application reference-count/role policy rejections are removed; decoder and model failures still propagate.

Rendering group 2 alone continues from the attached preceding group’s blend-length tail at local zero. Source trim is now passed explicitly to tail extraction, preventing an offset error for trimmed attachments. Prompts, media, selected checkpoint mode and timeline layout are preserved.

Focused conditioning/inventory checks passed. Legacy blend tests had retained a one-second assertion from before frame snapping; the actual native blend duration is 22/24 seconds and that expectation is updated. Extended model rendering was deferred at the user’s request.
