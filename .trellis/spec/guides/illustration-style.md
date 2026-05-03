# Illustration Generation Specification

This specification defines the preferred visual style for blog illustrations in this workspace.

Use it whenever generating or revising images for technical notes, especially AI engineering, agent workflow, quant engineering, and software quality articles.

---

## Target Style

The preferred style is a **hand-drawn technical notebook illustration**.

It should feel like:

- a sketch from a technical notebook,
- a classroom whiteboard diagram,
- a clean engineering explainer card,
- or a Chinese technical blog / Zhihu-style knowledge illustration.

It should not feel like:

- a dark SaaS landing-page hero,
- a glossy product marketing banner,
- a neon cyberpunk graphic,
- a generic AI stock image,
- or an abstract UI dashboard mockup.

---

## Visual Characteristics

### 1. Medium

Use a paper-based visual language:

- off-white paper,
- light graph paper,
- notebook page,
- drafting board,
- whiteboard,
- or hand-drawn slide.

The image should look drawn, annotated, and explained, not rendered as a polished app screen.

### 2. Linework

Prefer:

- pencil sketch lines,
- slightly imperfect hand-drawn outlines,
- gray or dark graphite strokes,
- thin technical diagram lines,
- hand-drawn arrows and callouts.

Avoid:

- glossy 3D rendering,
- hard vector corporate icons,
- heavy gradients,
- photorealism,
- dense UI cards.

### 3. Color Palette

Use muted, low-saturation color accents:

- pale green,
- light blue,
- beige,
- soft brown,
- warm gray,
- muted yellow.

The base should remain light and paper-like.

Avoid:

- dark navy/charcoal gradient backgrounds,
- neon cyan/purple glow,
- saturated commercial SaaS palettes,
- high-contrast cyberpunk colors.

### 4. Composition

Use concrete engineering metaphors:

- balance scales,
- maps and signposts,
- pipelines,
- drafting boards,
- greenhouse / incubator systems,
- circuit trees,
- checklists,
- index cards,
- books/manuals,
- flow arrows,
- machinery with visible inputs and outputs.

The image should explain an idea through a clear metaphor rather than simply decorate the page.

### 5. Text

Text should be sparse and legible.

Preferred text usage:

- one clear title,
- one short subtitle,
- 2-4 labels,
- short captions or callouts.

Avoid:

- long paragraphs inside the image,
- small dense text,
- many overlapping labels,
- decorative text that does not explain the concept.

For English articles, English text is preferred. Chinese labels are acceptable when the surrounding article or concept is Chinese-language.

### 6. Layout

Preferred layouts:

- side-by-side comparison,
- three-panel explanation,
- single metaphor with callout lines,
- matrix/table drawn as a whiteboard,
- process flow on a notebook page.

Keep the structure readable at blog width.

---

## Prompt Template

Use this template when asking for a generated illustration:

```text
Create a 16:9 hand-drawn technical blog illustration.
Style: off-white graph-paper or notebook background, pencil sketch linework,
subtle hand-drawn borders, muted watercolor accents in pale green, light blue,
beige, soft brown, and warm gray.

Topic: <topic>

Main metaphor: <concrete metaphor, e.g. map vs manual, greenhouse system,
balance scale, pipeline, drafting board>.

Text to include:
- Title: "<title>"
- Subtitle: "<subtitle>"
- Labels: "<short label 1>", "<short label 2>", ...

Composition:
<describe side-by-side / three panels / central object with callouts>.

Avoid dark gradients, glossy SaaS UI, neon colors, photorealism,
generic AI imagery, brand logos, and dense paragraphs.
Ensure all text is large and legible.
```

---

## Example: "Map, Not Manual"

Good direction:

- off-white graph-paper background,
- title: `Map, Not Manual`,
- left side: overloaded book/manual with tangled pages,
- right side: clean map board with `AGENTS.md` connected to `docs/`, `scripts/`, `tests/`, and `reference-projects/`,
- small icons such as compass, signpost, checklist, arrows,
- sparse explanatory text.

Bad direction:

- dark navy gradient,
- glowing SaaS dashboard cards,
- many generic UI boxes,
- neon cyber AI background,
- abstract "AI agent" icons without explanatory structure.

---

## Quality Checklist

Before accepting an illustration, check:

- [ ] Does it look like a technical notebook / hand-drawn explainer?
- [ ] Is the background light, paper-like, and not glossy?
- [ ] Are colors muted and low-saturation?
- [ ] Is the metaphor concrete and readable?
- [ ] Is the text sparse and legible?
- [ ] Does the image explain the article section rather than merely decorate it?
- [ ] Does it avoid dark SaaS hero aesthetics?

