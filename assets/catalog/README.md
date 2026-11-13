# ACC cars and circuits

`acc.json` records 54 distinct car variants across GT3, GT2, GT4, Cup, Challenge,
Super Trofeo, and TCX, plus 25 circuits. This is a content roster for current PC,
PS5, and Xbox Series releases, including paid DLC. It is not a statement that
all platforms share the same physics patch, or that every recording can be analyzed.

Each entry has a stable ID, display name, exact aliases, required content pack,
and source IDs. The `sources` array links to official ACC announcements, store
listings, and a documented in-game selection screen for legacy base-game cars.
Different model generations and racing classes retain separate IDs. A GT3 Cup
car is not a GT3 R; Nürburgring GP and the combined 24-hour layout are separate.

Season, weather, fuel, game version, and balance of performance belong to a
recording's conditions, not additional car or circuit identities. An ambiguous
name is left unresolved instead of assigning a model generation by guesswork.

The [reference library](../references/README.md) has its own coverage and source
checks. Roster membership does not mean a reference is available or its HUD has
been successfully extracted. Circuit geometry and its licenses are documented
in [the track-data guide](../tracks/README.md).
