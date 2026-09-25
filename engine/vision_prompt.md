Read the pool water-test screenshot at this exact path and report the numbers on it.

    {{IMAGE}}

Use the Read tool to open that file. It is a screenshot from a pool monitor app (usually an ICO
by Ondilo, sometimes a photo of a test strip result or a handheld meter). It shows current water
measurements as labelled numbers.

## The only rule that matters

Report ONLY what you can actually read on the image. This number becomes a dose of acid or a
bag of salt going into a real pool, so a confident guess is worse than a blank.

- If a value isn't shown on the screen, its key is `null`. Do not infer it from the others.
- If a value is shown but you can't read it confidently — blur, glare, a cut-off digit, an
  ambiguous decimal point — that key is `null` too, and you name it in `unreadable`.
- Never round to a "nicer" number and never correct one that looks implausible. 8.1 is 8.1 and
  564 is 564. Reporting what's on the screen is the entire job.
- Watch the decimal point on pH specifically: 7.8 and 78 are not the same reading, and only one
  of them is a pH.

## Units

Report bare numbers, no unit strings, in these units:

    ph              0-14, one or two decimals            e.g. 8.1
    orp_mv          millivolts, whole number             e.g. 564
    salt_ppm        ppm, whole number                    e.g. 2791
    water_temp_f    degrees FAHRENHEIT, whole number     e.g. 78
    cya_ppm         ppm (stabilizer / cyanuric acid)     e.g. 35
    ta_ppm          ppm (total alkalinity)               e.g. 80
    ch_ppm          ppm (calcium hardness)               e.g. 250
    fc_ppm          ppm (free chlorine)                  e.g. 3.2
    borates_ppm     ppm                                  e.g. 50

If the screen shows temperature in Celsius, convert it to Fahrenheit and say so in `notes`.
If it shows salt in g/L, multiply by 1000 to get ppm and say so in `notes`.

## If you can't read it at all

If the image isn't a water test, is too dark or blurry to read, or the Read tool can't open it,
set `"ok": false` and put one plain sentence in `notes` saying what went wrong. Every measurement
key is still present and `null`. Do not apologise and do not guess a single value.

## Output format

Return ONLY this JSON object — no prose before or after, no markdown fence:

{
  "ok": true,
  "ph": null,
  "orp_mv": null,
  "salt_ppm": null,
  "water_temp_f": null,
  "cya_ppm": null,
  "ta_ppm": null,
  "ch_ppm": null,
  "fc_ppm": null,
  "borates_ppm": null,
  "taken_at": null,
  "unreadable": [],
  "notes": ""
}

`taken_at` is the timestamp printed on the screenshot if there is one ("2026-04-18T09:14:00" or
just "2026-04-18"), otherwise null — not today's date, not a guess.
`unreadable` lists the keys you could see on screen but couldn't read confidently.
`notes` is one short sentence, or "".
