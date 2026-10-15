# Surface design rule

Every owner-facing decision card and selection is designed mobile first. The question and all actions must be visible without scrolling the page; long context scrolls inside the card, while the action controls remain pinned and reachable. Treat a hidden or clipped action as a defect, not a user-training issue.

## Browser decision surfaces

Use 360 × 560 CSS pixels as the compact proof viewport and 375 × 667 CSS pixels as the common phone proof viewport. Validate option labels, multi-line descriptions, errors, keyboard focus and the pinned action row at both sizes. The full-tab decision page is the fallback when a browser popup cannot open. Test the actual rendered surface, not only a component snapshot.

## Owner selections

Keep the prompt concise, put evidence and navigation detail in a separately scrollable region, and keep the action choices on screen. The suggested choice and alternatives must communicate their practical consequences without requiring the owner to search elsewhere.
