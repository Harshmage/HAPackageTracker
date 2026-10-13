# Supported carriers

Package Tracker never contacts a carrier. Detection works from the tracking number's
**format** and, where the format has one, its **check digit**. The result is a deep link
to the carrier's public tracking page. This page lists every format the detector knows,
the link templates, and how reliable each one is.

The code is in [`custom_components/package_tracker/carriers/`](../custom_components/package_tracker/carriers/):

- `rules.py` holds the format rules.
- `checksums.py` holds the check-digit algorithms.
- `registry.py` holds the carriers, link templates and link parsing.
- `classifier.py` ranks the candidates.
- `extract.py` finds numbers in free text.

## How detection works

1. **Normalize.** The input is NFKC-normalized and uppercased. Spaces, `-`, `.`, `_`,
   no-break spaces, unicode dashes and zero-width characters are removed. If the input
   is a carrier link (`https://…` or `www.…`), the number is read from the link instead.
2. **Canonicalize.**
   - A USPS `420` + ZIP routing prefix is removed. Both the ZIP5 and ZIP9 splits are
     tried; the one that leaves a valid IMpb wins, and ZIP9 wins if neither does.
   - A 34-digit FedEx Ground barcode is reduced to its last 12 digits.
   - The trailing `1` of a 17-character LaserShip label is removed.
3. **Match rules** (table below). Several carriers can match the same number, so every
   match becomes a candidate with a confidence between 0 and 1.
4. **Rank.**
   - Only the best candidate per carrier is kept.
   - A carrier name near the number in the text adds 0.15 (capped at 0.99).
   - Ties go to a valid check digit first, then a nearby keyword, then the home country,
     then registry order.
   - The card pre-selects the top candidate and lets you pick another.

Each rule also has an **acceptance policy**. It controls how much evidence
[extraction](#extraction-from-text) needs before it accepts a match found in free text:

| Policy | Meaning |
|---|---|
| `strong` | The format is distinctive on its own (`1Z…`, `TBA…`). Always accepted. |
| `checksum` | Digits only. Accepted only with a valid check digit **and** a word like "tracking", "shipment", "parcel", "package", "waybill", "AWB" or a carrier name within 80 characters. |
| `context` | A weak or ambiguous shape. Accepted only with a **carrier** keyword nearby. |

Typed or pasted numbers in the card's add form are always accepted. The policy only
applies to extraction.

## Format table

Confidence is shown as *valid check digit / invalid check digit*. "n/a" means the
format has no check digit.

| Rule id | Carrier | Pattern (canonical) | Service | Check digit | Confidence | Policy |
|---|---|---|---|---|---|---|
| `ups_1z` | UPS | `1Z` + 16 alphanumerics | from the service code (Ground, Next Day Air, …) | `ups_1z` | 1.0 / 0.7 ("possible typo") | strong |
| `ups_mi` | UPS | `MI` + 6 digits + 1–22 alphanumerics | Mail Innovations | n/a | 0.5 | context |
| `ups_t` | UPS | `T` + 10 digits | | n/a | 0.4 | context |
| `amazon_tba` | Amazon Logistics | `TB[ACM]` + 12 digits | | n/a | 0.95 | strong |
| `lasership_1ls` | OnTrac (LaserShip) | `1LS7[12]` + 10 digits | LaserShip | n/a | 0.9 | strong |
| `lasership_16` | OnTrac (LaserShip) | `1LS7[12]` + 2 digits + `01[1-4]` + 6 digits (17-char label minus the trailing `1`) | LaserShip | n/a | 0.9 | strong |
| `lasership_1lscx` | OnTrac (LaserShip) | `1LSCX` + 10 alphanumerics | LaserShip | n/a | 0.9 | strong |
| `lasership_l` | OnTrac (LaserShip) | `L[AIEHNX][1-3]` + 7 digits | LaserShip | n/a | 0.6 | context |
| `ontrac_cd` | OnTrac | `C` or `D` + 14 digits | | `ontrac` | 0.9 / 0.4 | checksum |
| `dhl_ecommerce_gm` | DHL | `GM` + 14–22 alphanumerics | eCommerce | n/a | 0.85 | strong |
| `dhl_jjd` | DHL | `JJD` or `JVGL` + 9–20 digits | Parcel | n/a | 0.85 | strong |
| `dhl_j` | DHL | `J` + 2–3 letters + 9–20 digits | Parcel | n/a | 0.5 | context |
| `dhl_express` | DHL | 10 or 11 digits | Express | `dhl_mod7` | 0.7 / 0.3 | checksum |
| `usps_impb` | USPS | `9[1-5]` + 20, 24 or 28 digits (22/26/30 total) | IMpb | `mod10_31` | 0.95 / 0.6 | checksum |
| `usps_20` | USPS | 20 digits, not starting `00` or `89` | | `mod10_31` (bare or `91`-prefixed) | 0.85 / 0.4 | checksum |
| `usps_20_iccid` | USPS | `89` + 18 digits (usually a SIM ICCID) | | `mod10_31` | 0.3 / 0.2 | context |
| `fedex_96` | FedEx | `96` + 20 digits | Ground | `mod10_31` over `[7:22]` | 0.9 / 0.5 | checksum |
| `fedex_sscc` | FedEx | `00` + 18 digits (not `00340434…`) | SSCC | `mod10_31` over the SSCC | 0.6 / 0.3 | checksum |
| `fedex_15` | FedEx | 15 digits | Ground | `mod10_31` | 0.8 / 0.4 | checksum |
| `fedex_12` | FedEx | 12 digits | Express | `fedex_mod11` | 0.6 / 0.3 | checksum |
| `fedex_34` | FedEx | 34-digit Ground barcode (canonicalized to the last 12) | Ground | `fedex_mod11` | 0.9 / 0.6 | checksum |
| `deutsche_post_sscc` | Deutsche Post | `00340434` + 12 digits | SSCC | `mod10_31` over the SSCC | 0.9 / 0.5 | checksum |
| `canada_post_16` | Canada Post | 16 digits | | n/a | 0.5 | context |
| S10 | postal operator from the suffix | 2 letters + 9 digits + ISO country code (`EA123456785US`) | EMS / Registered / Parcel / Insured / Tracked (S10) | `s10` | 0.95 / 0.6 | strong |
| S10 untracked | postal operator from the suffix | `U?` + 9 digits + country | Untracked (S10) | `s10` | 0.4 | context |

Special cases handled in `classifier.py`:

- **USPS handoffs.** FedEx Ground Economy, UPS SurePost and DHL eCommerce deliver through
  USPS with an IMpb label. Every IMpb therefore also offers FedEx, UPS and DHL as
  alternatives at 0.3. When the IMpb starts with `92612` (FedEx Ground Economy), FedEx
  rises to 0.6.
- **S10 origin and destination.** The suffix picks the origin post. The table is `US`
  usps, `CA` canada_post, `GB` royal_mail, `AU` australia_post, `DE` deutsche_post,
  `FR` la_poste, `JP` japan_post, `CN` china_post, `IN` india_post and `NL` postnl. Any
  other valid ISO 3166 code maps to "International Post".
  - Your home country's post (Home Assistant's configured country) is added as a
    destination alternative, 0.1 lower.
  - A suffix that is not an ISO 3166-1 alpha-2 code is rejected.
- **12-digit home-country alternatives.** In Germany and Japan, a 12-digit number also
  offers Deutsche Post or Japan Post, 0.05 below FedEx.
- **No catch-all.** The old `^[0-9A-Z]{12,14}$` "DHL" rule was removed on purpose.
  Numbers that match nothing are saved as **Unknown carrier**.

## Check-digit algorithms

All live in `checksums.py`. Each one is tested against the vectors in
[`tests/unit/test_checksums.py`](../tests/unit/test_checksums.py), and every
single-digit mutation of a vector must fail.

| Name | Used for | Algorithm |
|---|---|---|
| `ups_1z` | UPS `1Z` | The body is characters `[2:17]`. Letters map to `(ord(c) - ord('A') + 2) % 10`. Values at odd 0-based positions are doubled, with **no** digit-summing (unlike Luhn). `check = (10 - total % 10) % 10`. |
| `mod10_31` | USPS IMpb and 20-digit, FedEx 15 / 96, SSCC | Weights 3, 1, 3, … from the rightmost serial digit. `check = (10 - sum % 10) % 10`. A 20-digit USPS number is valid if the bare number or `"91" + number` passes. |
| `fedex_mod11` | FedEx 12-digit (and the 12 kept from 34-digit barcodes) | Weights 3, 1, 7 repeating, left to right, over the first 11 digits. `check = sum % 11 % 10`. |
| `dhl_mod7` | DHL Express 10/11 digits | `int(serial) % 7`. |
| `s10` | UPU S10 | Weights 8, 6, 4, 2, 3, 5, 9, 7 over the 8 serial digits. `check = 11 - sum % 11`, then 10 becomes 0 and 11 becomes 5. |
| `ontrac` | OnTrac `C`/`D` | Replace `C` with 4 or `D` with 5. Double odd 0-based positions with no digit-summing. `check = (10 - sum % 10) % 10`. |

## Tracking-page templates

`{tn}` is replaced with the canonical number, URL-encoded with
`urllib.parse.quote(tn, safe="")`. A per-package **Tracking link** (`tracking_url`)
always takes priority over the template.

> **Honesty note.** These templates use the URL formats carriers publish and that
> open-source trackers use. They have **not** been checked live in a browser for this
> release (2026-10). Carriers change their sites without notice. If a link breaks, set a
> per-package tracking link, use the fallback site, and open an issue.

| Carrier id | Name | Template | Last verified |
|---|---|---|---|
| `ups` | UPS | `https://www.ups.com/track?loc=en_US&tracknum={tn}` | format from public references, not live-verified (2026-10) |
| `usps` | USPS | `https://tools.usps.com/go/TrackConfirmAction?tLabels={tn}` | format from public references, not live-verified (2026-10) |
| `fedex` | FedEx | `https://www.fedex.com/fedextrack/?trknbr={tn}` | format from public references, not live-verified (2026-10) |
| `dhl` | DHL (Express, Parcel, eCommerce) | `https://www.dhl.com/global-en/home/tracking.html?tracking-id={tn}&submit=1` | format from public references, not live-verified (2026-10) |
| `amazon` | Amazon Logistics | `https://track.amazon.com/tracking/{tn}` (may ask you to sign in) | format from public references, not live-verified (2026-10) |
| `ontrac` | OnTrac (incl. LaserShip) | `https://www.ontrac.com/tracking/?number={tn}` | format from public references, not live-verified (2026-10) |
| `canada_post` | Canada Post | `https://www.canadapost-postescanada.ca/track-reperage/en#/search?searchFor={tn}` | format from public references, not live-verified (2026-10) |
| `royal_mail` | Royal Mail | `https://www.royalmail.com/track-your-item#/tracking-results/{tn}` | format from public references, not live-verified (2026-10) |
| `australia_post` | Australia Post | `https://auspost.com.au/mypost/track/#/details/{tn}` | format from public references, not live-verified (2026-10) |
| `deutsche_post` | Deutsche Post | `https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={tn}` | format from public references, not live-verified (2026-10) |
| `la_poste` | La Poste | `https://www.laposte.fr/outils/suivre-vos-envois?code={tn}` | format from public references, not live-verified (2026-10) |
| `japan_post` | Japan Post | `https://trackings.post.japanpost.jp/services/srv/search/direct?reqCodeNo1={tn}&locale=en` | format from public references, not live-verified (2026-10) |
| `china_post` | China Post | none. Homepage `https://www.chinapost.com.cn` plus copy. | n/a |
| `india_post` | India Post | none. Homepage `https://www.indiapost.gov.in` plus copy. | n/a |
| `postnl` | PostNL | none. Homepage `https://www.postnl.nl` plus copy. | n/a |
| `intl_post` | International Post | none. Homepage `https://www.upu.int` plus copy. | n/a |
| `unknown` | Unknown carrier | none. Homepage `https://www.17track.net/en` plus copy. | n/a |

When you verify a template, change its row to the date and browser you used, for
example `2026-11-02 (Firefox)`.

### Fallback trackers

The **Fallback tracking site** option adds a second link to every package, including
ones that already have a deep link. It is most useful for unknown and international
numbers.

| Option | Template |
|---|---|
| `17track` (default) | `https://t.17track.net/en#nums={tn}` |
| `parcelsapp` | `https://parcelsapp.com/en/tracking/{tn}` |
| `none` | no fallback link |

## Reading numbers from links

A pasted carrier link, in the add form, the paste dialog, `add_package` or e-mail text,
is parsed before format detection.

1. Redirect wrappers are unwrapped first, up to two levels. These are Outlook SafeLinks
   and other `url=`, `q=`, `u=`, `target=`, `redirect=` and `dest=` parameters.
2. On a carrier's own host, only that carrier's tracking parameters are read:

| Carrier | Hosts (suffix match) | Parameters |
|---|---|---|
| UPS | `ups.com` | `tracknum`, `InquiryNumber1`, `tracknumbers`, `trackNums` |
| USPS | `usps.com` | `tLabels`, `qtc_tLabels1` |
| FedEx | `fedex.com` | `trknbr`, `tracknumbers` |
| DHL | `dhl.com` | `tracking-id`, `AWB` |
| Amazon Logistics | `track.amazon.com` | last path segment |
| OnTrac | `ontrac.com`, `lasership.com` | `number`, `tracking_number`, `trackingNumber` |
| Canada Post | `canadapost-postescanada.ca`, `canadapost.ca` | `searchFor` (also in the `#` fragment) |
| Royal Mail | `royalmail.com` | last path segment (also in the `#` fragment) |
| Australia Post | `auspost.com.au` | last path segment (also in the `#` fragment) |
| Deutsche Post | `dhl.de`, `deutschepost.de` | `piececode`, `idc` |
| La Poste | `laposte.fr` | `code` |
| Japan Post | `japanpost.jp` | `reqCodeNo1` |

On any other host, a value is read only from a parameter whose name looks
tracking-related (`track`, `trk`, `tn`, `label`, `awb`, `number`, `piece`, `shipment`,
`inquiry`). It is accepted only at confidence ≥ 0.9. This keeps marketing click ids out.

## Extraction from text

`extract_tracking_numbers` and the card's paste dialog take e-mail text, plain or HTML.
Processing runs in this order:

1. Decode quoted-printable text.
2. Strip `<head>`, `<style>` and `<script>`, keep link targets, and unescape entities.
3. Run the link pass, then remove URLs from the text.
4. Scan contiguous 8–40 character tokens.
5. Scan grouped runs separated by spaces or hyphens, such as `9400 1096 9993 …`. These
   always need a keyword nearby.

These negative filters reject:

- phone numbers
- Amazon order ids (`123-1234567-1234567`)
- ZIP+4 codes
- dates and times
- repeated or sequential digit runs, except in `strong` formats
- numbers preceded by words such as ORDER, INVOICE, CONFIRMATION, ACCOUNT, REFERENCE,
  PHONE, SKU, ITEM, IMEI, ICCID, SERIAL, GIFT CARD, PIN, MEMBER, CUSTOMER, CASE, TICKET
  or CARD ENDING

Input is capped at 100,000 characters and output at 50 numbers. Results are de-duplicated
by canonical number and kept in order of first appearance.

## Adding or changing a carrier

See [development.md, "Adding a carrier"](development.md#adding-a-carrier).

## References

- [jkeen/tracking_number_data](https://github.com/jkeen/tracking_number_data) (MIT).
  Format definitions and test vectors from this project were used to check the rules and
  checksums. It is a reference only and is not vendored.
- [UPU S10 standard](https://www.upu.int/en/postal-solutions/programmes-services/addressing-solutions)
  covers the international item identifier, its check digit and country suffix.
- [USPS Publication 199](https://postalpro.usps.com/pub199) covers the IMpb barcode
  specification, `420` ZIP routing and the mod-10 check digit.
- [GS1 check digit calculator](https://www.gs1.org/services/how-calculate-check-digit-manually)
  covers the mod-10 (3, 1) algorithm used for SSCC, IMpb and FedEx Ground.
- [17TRACK](https://www.17track.net/en) and [Parcels](https://parcelsapp.com/en) are the
  universal tracking sites used as fallbacks.
