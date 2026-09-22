// Day color palette — distinct, dark-UI-friendly, colorblind-tolerant.
// Shared by the map (legend, markers, route lines) and the itinerary tab
// (day accents, stop number badges) so a day is the same color everywhere.
export const DAY_COLORS = [
  '#e6194B', // red
  '#3cb44b', // green
  '#4363d8', // blue
  '#f58231', // orange
  '#911eb4', // purple
  '#42d4f4', // cyan
  '#f032e6', // magenta
  '#bfef45', // lime
  '#fabed4', // pink
  '#469990', // teal
]

export const dayColor = (i) => DAY_COLORS[i % DAY_COLORS.length]
