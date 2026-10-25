---
name: VIIO
description: Employee assistant and administration in one workspace.
colors:
  canvas: "#f5f6f9"
  surface: "#fff"
  subtle: "#f0f2f6"
  hover: "#e9ecf3"
  ink: "#202735"
  muted: "#596376"
  line: "#dce1ea"
  accent: "#4f46d9"
  accent-hover: "#4037be"
  accent-soft: "#ebeafb"
  positive: "#16704b"
  positive-soft: "#e7f5ed"
  negative: "#b52f3d"
  negative-soft: "#fcecef"
  dark-canvas: "#13161d"
  dark-surface: "#1a1e27"
  dark-subtle: "#202530"
  dark-hover: "#2b3140"
  dark-ink: "#edf0f6"
  dark-muted: "#a3afc2"
  dark-line: "#343c4c"
  dark-accent: "#a49eff"
  dark-accent-hover: "#c3bfff"
  dark-accent-soft: "#2c294a"
  dark-positive: "#80d6ac"
  dark-positive-soft: "#193b2d"
  dark-negative: "#ff9ca9"
  dark-negative-soft: "#42232d"
typography:
  headline:
    fontFamily: "'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "clamp(28px, 3vw, 38px)"
    fontWeight: 650
    lineHeight: 1.18
    letterSpacing: "-.03em"
  title:
    fontSize: "30px"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-.025em"
  section:
    fontSize: "16px"
    fontWeight: 650
    lineHeight: 1.5
  body:
    fontFamily: "'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "14px"
  label:
    fontSize: "13px"
    fontWeight: 600
rounded:
  badge: "6px"
  control: "8px"
  compact: "10px"
  surface: "12px"
  composer: "16px"
spacing:
  icon-gap: "8px"
  control-gap: "12px"
  compact: "16px"
  mobile-panel: "20px"
  panel: "24px"
  form: "28px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
    typography: "{typography.label}"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 12px"
  surface-section:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.surface}"
    padding: "24px"
  status-good:
    backgroundColor: "{colors.positive-soft}"
    textColor: "{colors.positive}"
    rounded: "{rounded.badge}"
    padding: "5px 8px"
---

# Design System: VIIO

## Overview

**Creative North Star: "Рабочее пространство VIIO"**

The administration extension retains the incumbent indigo accent, neutral light and dark themes, sidebar navigation, and Lucide icons. Its direction is a functional workspace for finding users, managing access, and adding company knowledge. This is a record of the implemented system and the direction contract in `docs/admin-ui.md`, not a new brand identity.

The interface uses compact controls and tables, clear page headings, and generous separation between task groups. Color identifies selection, primary actions, and explicit service or submission states. No production raster assets were added; `.impeccable/screenshots/` contains fixture QA captures, not shipping assets.

**Key Characteristics:**
- Indigo actions and selected navigation.
- Neutral surfaces with shared light and dark semantic tokens.
- Compact operational controls with clearly separated task groups.
- Text, icons, and state color used together.

## Colors

### Primary

Indigo is the action and selection accent. Use `accent` for primary actions, links, focus indicators, and selected navigation; `accent-soft` supports selected regions. The hover token follows the active theme.

### Neutral

Canvas frames the workspace; surface holds forms and panels; subtle separates headers and rows; hover marks interactive regions. Ink is primary text, muted is supporting text, and line is the shared boundary color. The frontmatter records both theme sets; `.dark` replaces the CSS custom-property values rather than changing component markup.

Positive and negative pairs communicate service availability and submission feedback. They are semantic states, not additional brand accents. Status badges include text and a dot; notices use `status` or `alert` semantics.

**The Theme Pair Rule.** Reuse the semantic CSS variables from `WebChat/src/index.css` so surfaces, text, borders, and state colors change together.

## Typography

The system stack is Segoe UI with platform fallbacks. There is no imported display font. Body text is the frontmatter body role; line height is set per context, including page descriptions (1.6), welcome copy (1.7), and form inputs (1.5).

The headline role is the chat welcome heading. Administration uses the title role, reduced to 26px on mobile; the welcome heading becomes 29px. Section headings use the section role. Supporting table labels, badges, and metadata use 11–12px; controls and row content commonly use 13px. Numeric statistics and dates use tabular figures. Page descriptions and document help are constrained to 65ch where defined.

## Layout

The app occupies `100dvh` with a fixed-height flex shell and independently scrolling content. Desktop header minimum height is 72px. The chat sidebar is 272px; administration sidebar is 232px. Administration content is centered within 1180px and uses 42px vertical / 44px horizontal padding. Overview columns use 1.2fr / 1fr; knowledge uses a flexible form and 250px help column. Four statistics share a divided row. The chat composer is capped at 850px; messages at 860px.

At widths up to 1199px, administration padding becomes 32px / 26px, its sidebar becomes 210px, and overview/knowledge layouts become one column. At widths up to 767px, the header wraps (108px minimum), administration navigation becomes horizontal, content padding becomes 28px / 20px, statistics use two columns, and form/access-option grids become one column. Panels use 20px padding on mobile. Chat navigation becomes a dismissible overlay up to `min(300px, 86vw)`; app logic uses 768px as the desktop threshold.

The user table retains a 780px minimum width and scrolls inside its bordered container. Preserve that internal overflow rather than widening the page.

## Elevation & Depth

Depth is mostly tonal layering and one-pixel borders. Panels do not have general-purpose shadows. The composer alone has a subtle `0 4px 20px #00000005` shadow. Mobile chat navigation uses a `#0007` scrim. Keyboard focus uses a two-pixel accent outline with a three-pixel offset; search uses an accent border and soft outline. These are state indicators rather than elevation.

Color and border transitions last 160ms with CSS default easing. Reduced-motion mode shortens animation and transition durations to .01ms, limits animation to one iteration, and removes smooth scrolling.

## Shapes

Controls and navigation have gently curved corners using the control radius. Panels and table containers use the surface radius; the composer uses the larger composer radius. Badges use the badge radius, while the small status dot is circular. The user avatar uses the compact radius. Borders are generally a single line; upload selection uses a dashed line, and the open user editor uses an accent border.

## Components

### Buttons

Primary and secondary buttons share 10px / 16px padding, 42px minimum height, 13px semibold text, and an 8px icon gap. Primary uses accent over surface-colored text; secondary uses surface, ink, and a line border. Hover changes the relevant background. Icon buttons are 36px square. Text actions use accent and underline on hover. Disabled buttons have .55 opacity and a not-allowed cursor; the send button has its own neutral disabled treatment.

### Inputs / Fields

Fields use surface, ink, a line border, control corners, and 11px / 12px padding. Labels remain visible above inputs. Focus adds the accent border and keyboard outline. Help text uses muted color. The search field wraps its input and icon, with focus on the shared container. Textareas can resize vertically.

### Navigation

The header switcher groups assistant and administration destinations. Selected items use accent text and a surface background on desktop, accent-soft on mobile. Administration sidebar items use accent-soft and heavier text when selected. Preserve `aria-current`, descriptive icon-button labels, and the skip-to-content link.

### Cards / Containers

Service/action panels use surface, a line border, the surface radius, and panel spacing. Tables share this enclosure with subtle header and hover rows. The user editor adds an accent border. Document forms use 28px padding before the mobile reduction. Avoid adding decorative shadows to these implemented flat surfaces.

### Badges and feedback

Role badges use subtle/muted; administrator badges use accent-soft/accent. Service badges use the corresponding positive or negative pair and readable state text. Notices use the same state pair with 13px text and 1.6 line height. Loading, empty, error, and success states are visible parts of the task flow.

### Access choices and composer

Knowledge access choices are labeled radio controls with a border, help text, and accent-soft selection. They appear in two columns before the mobile collapse. The chat composer uses the larger radius, accent border on focus, a resize-free text area, and a 40px square send control.

## Do's and Don'ts

### Do:
- **Do** reuse semantic colors in both themes.
- **Do** preserve visible labels, keyboard focus, and text accompanying state colors.
- **Do** keep table overflow inside its container and collapse form grids at the implemented mobile breakpoint.
- **Do** treat the screenshots as fixture QA evidence, not production content or interface assets.

### Don't:
- **Don't** introduce a new brand palette or display font as part of the administration extension.
- **Don't** replace the implemented flat panel treatment with decorative card shadows.
- **Don't** present fixture counts as production measurements.
