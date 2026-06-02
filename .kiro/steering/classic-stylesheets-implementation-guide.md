---
inclusion: fileMatch
fileMatchPattern: 'operations-automation/anycompany-it-demo-portal/frontend/*'
---

# Classic Stylesheets Implementation Guide

A comprehensive guide for implementing authentic retro desktop styling using our custom classic stylesheets, based on analysis of the CDE, Manzana, and Ventana themes used in the GenAI Ops Demo Library.

## Overview

Our classic stylesheets recreate authentic retro desktop environments from the 1990s era, providing three distinct themes that evoke different computing platforms without using commercial references. This guide provides implementation patterns and best practices for consistent usage across demos.

## Available Themes

### CDE Theme (`cde.css`)
**Unix Workstation Interface**
- **Color Palette**: Purple desktop (`#63639C`), light gray windows (`#DDDDDD`)
- **Typography**: Sans-serif, 12px
- **Style**: Clean, professional Unix workstation aesthetic
- **Use Case**: Technical/enterprise applications, IT management tools

### Manzana Theme (`manzana.css`)
**Classic Desktop Interface**
- **Color Palette**: Blue-purple desktop (`#9999CC`), light gray windows (`#DDDDDD`)
- **Typography**: Geneva/Helvetica, 12px
- **Style**: Rounded corners, gradient buttons, classic Mac-inspired
- **Use Case**: User-friendly applications, general business tools

### Ventana Theme (`ventana.css`)
**PC-Style Interface**
- **Color Palette**: Teal desktop (`#008080`), silver windows (`#c0c0c0`)
- **Typography**: Tahoma/Arial, 11px
- **Style**: Sharp edges, outset borders, Windows 95-inspired
- **Use Case**: System utilities, administrative tools

## Core CSS Architecture

### CSS Custom Properties Pattern
All themes use consistent CSS custom properties for maintainability:

```css
:root {
  --desktop-bg: /* Theme-specific desktop color */
  --window-bg: /* Window background color */
  --window-fg: /* Text color */
  --button-bg: /* Button background */
  --button-shadow: /* Shadow/border colors */
  --button-highlight: /* Highlight colors */
  --element-spacing: 8px; /* Consistent spacing unit */
}
```

### Universal Layout Classes
All themes provide consistent utility classes:

```css
.flex-row { display: flex; gap: var(--element-spacing); align-items: center; }
.gap { gap: var(--element-spacing); }
.margin-bottom { margin-bottom: var(--element-spacing); }
.margin-top { margin-top: var(--element-spacing); }
.padding { padding: var(--element-spacing); }
```

## Component Implementation Patterns

### Window Structure
**Standard Pattern** (consistent across all themes):
```html
<div class="window active">
  <div class="title-bar">
    <div class="title-bar-text">Window Title</div>
    <div class="title-bar-buttons">
      <button onclick="goBack()">←</button>
      <button data-minimize></button>
      <button data-maximize></button>
      <button data-close></button>
    </div>
  </div>
  <div class="window-body padding">
    <!-- Window content -->
  </div>
</div>
```

**Key Elements**:
- `.window.active`: Main window container with active state
- `.title-bar`: Window header with title and controls
- `.title-bar-buttons`: Container for window control buttons
- `.window-body.padding`: Content area with consistent padding

### Button Patterns
**Standard Buttons**:
```html
<button>Standard Button</button>
<button class="primary">Primary Action</button>
<button disabled>Disabled Button</button>
```

**Button Behavior**:
- All themes provide `:active` states with inset/pressed appearance
- Primary buttons have enhanced borders/styling
- Disabled buttons use muted colors

### Form Elements
**Input Fields**:
```html
<input type="text" placeholder="Enter text...">
<textarea rows="4"></textarea>
<select>
  <option>Option 1</option>
  <option>Option 2</option>
</select>
```

**Form Layout Pattern**:
```html
<div class="flex-row gap">
  <div>
    <label>Field Label:</label>
    <input type="text" placeholder="Value">
  </div>
</div>
```

### Table Implementation
**Standard Table Structure**:
```html
<table class="detailed">
  <thead>
    <tr>
      <th scope="col" role="button">Column Header</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Cell Content</td>
    </tr>
  </tbody>
</table>
```

**Table Features**:
- Consistent border styling across themes
- Hover states for row selection
- Header styling with outset borders

### Fieldset and Legend
**Grouping Pattern**:
```html
<fieldset>
  <legend>Section Title</legend>
  <!-- Grouped content -->
</fieldset>
```

**Styling**:
- Groove/inset borders for visual grouping
- Legend backgrounds match window background
- Consistent padding using `--element-spacing`

## Field Alignment Standards

### Label-Value Pairs (for detail views)
**Recommended Pattern**:
```html
<div class="field-row">
  <label style="width: 120px; display: inline-block; text-align: right;">
    <strong>Field Name:</strong>
  </label>
  <span style="margin-left: 8px;">Field Value</span>
</div>
```

**Standards**:
- **Label Width**: `120px` (accommodates longest labels)
- **Label Alignment**: `text-align: right` (creates clean column)
- **Label Display**: `display: inline-block` (enables width control)
- **Value Spacing**: `margin-left: 8px` (consistent gap)

### Form Input Layout
**Input Field Pattern**:
```html
<div class="flex-row gap">
  <div>
    <label>Input Label:</label>
    <input type="text" style="width: 250px;">
  </div>
</div>
```

**Input Standards**:
- **Text Inputs**: `250px` width for standard fields
- **Number Inputs**: `100px` width for numeric values
- **Consistent Spacing**: Use `.flex-row.gap` for form layouts

## Theme-Specific Considerations

### CDE Theme Specifics
- Uses `outset`/`inset` borders for 3D effect
- Focus states use `--focus-color: #B24D7A`
- Table hover uses focus color for selection
- Clean, professional appearance

### Manzana Theme Specifics
- Rounded corners (`border-radius: 3px-8px`)
- Gradient backgrounds on buttons
- Box shadows for depth (`inset` shadows)
- Dropdown arrows using Unicode `▼`
- Selection background: `--selection-bg: #ccf`

### Ventana Theme Specifics
- Sharp, angular design (no border-radius)
- Linear gradients on title bars
- Outset/inset borders for 3D appearance
- Consistent font size (12px) for improved readability
- Classic PC color scheme

## Implementation Best Practices

### 1. Theme Selection
**Choose based on demo context**:
- **CDE**: Technical/enterprise tools, system administration
- **Manzana**: User-friendly business applications
- **Ventana**: System utilities, configuration tools

### 2. Consistent Spacing
**Always use CSS custom properties**:
```css
/* Good */
padding: var(--element-spacing);
gap: var(--element-spacing);

/* Avoid */
padding: 8px; /* Hardcoded values */
```

### 3. Color Usage
**Use theme color variables**:
```css
/* Good */
background: var(--window-bg);
color: var(--window-fg);

/* Avoid */
background: #DDDDDD; /* Hardcoded colors */
```

### 4. Button States
**Ensure all interactive elements have proper states**:
```css
button:hover { /* Hover feedback */ }
button:active { /* Pressed state */ }
button:disabled { /* Disabled appearance */ }
```

### 5. Accessibility
**Include proper ARIA attributes**:
```html
<th scope="col" role="button">Sortable Column</th>
<div role="listbox" aria-multiselectable="true">
  <!-- Selectable content -->
</div>
```

## Common Implementation Patterns

### Modal Dialogs
```html
<div id="modalBackdrop" onclick="hideModal()" style="display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0, 0, 0, 0.5); z-index: 999;"></div>
<div class="dialog active" id="modal" style="display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 1000;">
  <div class="title-bar">
    <div class="title-bar-text">Dialog Title</div>
    <div class="title-bar-buttons">
      <button data-close onclick="hideModal()"></button>
    </div>
  </div>
  <div class="window-body padding">
    <!-- Dialog content -->
  </div>
</div>
```

### Data Tables with Actions
```html
<table class="detailed">
  <thead>
    <tr>
      <th><input type="checkbox" onchange="toggleSelectAll()"></th>
      <th>Data Column</th>
      <th>Actions</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><input type="checkbox" value="item-id"></td>
      <td>Data Value</td>
      <td>
        <button onclick="editItem('item-id')">Edit</button>
        <button onclick="deleteItem('item-id')">Delete</button>
      </td>
    </tr>
  </tbody>
</table>
```

### Search and Filter Sections
```html
<fieldset>
  <legend>Search & Filter</legend>
  <div class="flex-row gap">
    <div>
      <label>Search:</label>
      <input type="text" placeholder="Search items..." onkeyup="filterItems()">
    </div>
    <div>
      <label>Category:</label>
      <select onchange="filterItems()">
        <option value="">All Categories</option>
        <option value="category1">Category 1</option>
      </select>
    </div>
    <button onclick="clearFilters()">Clear Filters</button>
  </div>
</fieldset>
```

## Testing and Validation

### Visual Consistency Checklist
- [ ] All windows use proper `.window.active` structure
- [ ] Title bars have consistent button layout
- [ ] Form elements follow spacing standards
- [ ] Tables have proper hover states
- [ ] Buttons show active/pressed states
- [ ] Colors use CSS custom properties
- [ ] Spacing uses `--element-spacing` variable

### Cross-Theme Compatibility
- [ ] HTML structure works with all three themes
- [ ] No theme-specific hardcoded values
- [ ] Consistent class names across implementations
- [ ] Proper fallback fonts specified

## Integration Examples

### Complete Portal Implementation
See `operations-automation/anycompany-it-demo-portal/frontend/` for complete examples:
- **itsm.html**: CDE theme implementation
- **inventory.html**: Ventana theme implementation  
- **procurement.html**: Manzana theme implementation

Each demonstrates proper theme usage, form layouts, table implementations, and modal dialogs following these guidelines.

## Maintenance Guidelines

### Adding New Components
1. **Follow existing patterns** from the three themes
2. **Use CSS custom properties** for colors and spacing
3. **Test across all themes** to ensure compatibility
4. **Document new patterns** in this guide

### Theme Updates
1. **Maintain consistency** across all three themes
2. **Update CSS custom properties** rather than hardcoded values
3. **Test existing implementations** after changes
4. **Version control** theme changes carefully

---

*This guide is based on analysis of the actual CSS implementations in the GenAI Ops Demo Library and should be updated as new patterns emerge.*