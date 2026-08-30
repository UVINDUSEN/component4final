// lib/features/patients/aura_qr.dart
//
// ONE place that knows what an Aura QR looks like.
//
// THE BUG THIS FIXES
// ------------------
// Aura renders its QR from share_participant_id_page.dart:
//
//     String get _qrData => 'clinanx://patient/$participantId';
//
// ScanPatientIdScreen read the raw value, uppercased it, and tested it against
// `^P_[A-F0-9]{16}$`. The scanner therefore saw
//
//     CLINANX://PATIENT/P_8A0840A798B81072
//
// which does not match, so it rejected every genuine Aura code and told the
// clinician "That code is not an Aura Participant ID." The two apps were built
// against two different ideas of the payload and neither was wrong on its own.
//
// Fixing the DECODER rather than the ENCODER is deliberate. The scheme prefix is
// what makes the QR a deep link — Aura wants it, and stripping it from the
// patient app would break any future "open in Aura" behaviour. It also means
// clinicians already carrying a build of Aura in the ward keep working without
// a patient-side update.
//
// WHAT IS STILL REJECTED
// ----------------------
// Everything that is not a participant id. A camera pointed at a ward finds
// wristbands, drug GTINs and asset tags; accepting the first decodable thing
// would enrol a patient under a medication barcode. The shape check is
// unchanged, it just now runs on the right substring.

/// Canonical shape of an Aura participant id: `P_` + 16 uppercase hex digits.
/// Must stay in step with ParticipantIdentityService._participantPattern in the
/// patient app and with ChartController._participantIdPattern in this one.
final RegExp kAuraParticipantId = RegExp(r'^P_[A-F0-9]{16}$');

/// Accepted QR envelopes, most specific first.
final RegExp _auraDeepLink =
    RegExp(r'^clinanx://patient/(.+)$', caseSensitive: false);

/// Returns the participant id encoded in [raw], or null if [raw] is not an Aura
/// code at all.
///
/// Accepts both the deep-link envelope Aura currently emits and a bare id, so a
/// clinician can also type the id by hand into the same validator.
String? decodeAuraQr(String? raw) {
  if (raw == null) return null;

  var value = raw.trim();
  if (value.isEmpty) return null;

  final match = _auraDeepLink.firstMatch(value);
  if (match != null) {
    value = match.group(1)!.trim();
  }

  // Strip a trailing slash or query string if a future Aura build adds one,
  // e.g. clinanx://patient/P_ABC...?v=2
  final cut = value.indexOf(RegExp(r'[?#/]'));
  if (cut > 0) value = value.substring(0, cut);

  value = value.toUpperCase();
  return kAuraParticipantId.hasMatch(value) ? value : null;
}
