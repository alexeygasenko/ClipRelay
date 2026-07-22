(() => {
  const states = new WeakMap();

  function normalizedSources(sources) {
    return [...new Set((sources || []).filter((source) => typeof source === "string" && source))];
  }

  function animateImageChange(carousel, image, source, direction) {
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!direction || reduceMotion || typeof image.animate !== "function" || !image.src) {
      image.src = source;
      return;
    }
    carousel.querySelectorAll("[data-image-carousel-outgoing]").forEach((item) => item.remove());
    image.getAnimations?.().forEach((animation) => animation.cancel());
    const outgoing = image.cloneNode(false);
    outgoing.removeAttribute("data-image-carousel-image");
    outgoing.setAttribute("data-image-carousel-outgoing", "");
    outgoing.alt = "";
    image.parentElement.insertBefore(outgoing, image);
    image.src = source;
    const distance = direction * 9;
    const timing = { duration: 380, easing: "cubic-bezier(.22,.78,.18,1)", fill: "both" };
    outgoing.animate([
      { opacity: 1, transform: "translateX(0) scale(1)", filter: "blur(0)" },
      { opacity: 0, transform: `translateX(${-distance}%) scale(.985)`, filter: "blur(3px)" },
    ], timing).finished.catch(() => {}).finally(() => outgoing.remove());
    image.animate([
      { opacity: 0, transform: `translateX(${distance}%) scale(.985)`, filter: "blur(3px)" },
      { opacity: 1, transform: "translateX(0) scale(1)", filter: "blur(0)" },
    ], timing);
  }

  function showImage(carousel, requestedIndex, scrollThumbnail = false, direction = 0) {
    const state = states.get(carousel);
    if (!state || !state.sources.length) return;
    const count = state.sources.length;
    const nextIndex = (requestedIndex + count) % count;
    const image = carousel.querySelector("[data-image-carousel-image]");
    if (nextIndex !== state.index || !image.src) {
      animateImageChange(carousel, image, state.sources[nextIndex], direction);
    }
    state.index = nextIndex;
    image.alt = `Изображение ${state.index + 1} из ${count}`;
    carousel.querySelectorAll("[data-image-carousel-counter]").forEach((counter) => {
      counter.textContent = `${state.index + 1} / ${count}`;
    });
    carousel.querySelectorAll("[data-image-carousel-thumbnail]").forEach((thumbnail) => {
      const selected = Number(thumbnail.dataset.imageCarouselThumbnail) === state.index;
      thumbnail.classList.toggle("active", selected);
      thumbnail.setAttribute("aria-current", selected ? "true" : "false");
      if (selected && scrollThumbnail) {
        thumbnail.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      }
    });
  }

  function renderThumbnails(carousel, sources) {
    const rail = carousel.querySelector("[data-image-carousel-thumbnails]");
    if (!rail) return;
    rail.replaceChildren();
    sources.forEach((source, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.imageCarouselThumbnail = String(index);
      button.dataset.imageSource = source;
      button.setAttribute("aria-label", `Показать изображение ${index + 1}`);
      const image = document.createElement("img");
      image.src = source;
      image.alt = "";
      image.loading = "lazy";
      button.appendChild(image);
      rail.appendChild(button);
    });
  }

  function setSources(carousel, sources, render = true) {
    if (!carousel) return;
    const values = normalizedSources(sources);
    if (render) renderThumbnails(carousel, values);
    states.set(carousel, { sources: values, index: 0 });
    carousel.hidden = values.length === 0;
    const multiple = values.length > 1;
    carousel.querySelectorAll("[data-image-carousel-prev], [data-image-carousel-next]").forEach((button) => {
      button.hidden = !multiple;
    });
    const rail = carousel.querySelector("[data-image-carousel-thumbnails]");
    if (rail) rail.hidden = !multiple;
    if (values.length) showImage(carousel, 0);
  }

  function updateSelection(carousel, changedInput = null) {
    const inputs = [...carousel.querySelectorAll("[data-image-selection]")];
    if (!inputs.length) return;
    let selected = inputs.filter((input) => input.checked);
    if (!selected.length && changedInput) {
      changedInput.checked = true;
      selected = [changedInput];
      carousel.classList.remove("image-selection-warning");
      requestAnimationFrame(() => carousel.classList.add("image-selection-warning"));
      window.setTimeout(() => carousel.classList.remove("image-selection-warning"), 520);
    }
    inputs.forEach((input) => {
      input.closest("[data-image-selection-item]")?.classList.toggle(
        "image-included", input.checked
      );
    });
    carousel.querySelectorAll("[data-image-selection-count]").forEach((counter) => {
      counter.textContent = String(selected.length);
    });
    carousel.querySelectorAll("[data-image-selection-all]").forEach((button) => {
      button.disabled = selected.length === inputs.length;
    });
  }

  function initialize(root = document) {
    const carousels = root.matches?.("[data-image-carousel]")
      ? [root]
      : [...root.querySelectorAll("[data-image-carousel]")];
    carousels.forEach((carousel) => {
      if (states.has(carousel)) return;
      const sources = [...carousel.querySelectorAll("[data-image-carousel-thumbnail]")]
        .map((thumbnail) => thumbnail.dataset.imageSource);
      setSources(carousel, sources, false);
      updateSelection(carousel);
    });
  }

  function move(carousel, offset) {
    const state = states.get(carousel);
    if (state) showImage(carousel, state.index + offset, true, Math.sign(offset));
  }

  document.addEventListener("click", (event) => {
    const control = event.target.closest(
      "[data-image-carousel-prev], [data-image-carousel-next], [data-image-carousel-thumbnail]"
    );
    if (!control) return;
    const carousel = control.closest("[data-image-carousel]");
    if (!carousel) return;
    event.preventDefault();
    if (control.matches("[data-image-carousel-prev]")) move(carousel, -1);
    else if (control.matches("[data-image-carousel-next]")) move(carousel, 1);
    else {
      const state = states.get(carousel);
      const nextIndex = Number(control.dataset.imageCarouselThumbnail);
      const direction = state ? Math.sign(nextIndex - state.index) : 0;
      showImage(carousel, nextIndex, true, direction);
    }
  });

  document.addEventListener("keydown", (event) => {
    const carousel = event.target.closest?.("[data-image-carousel]");
    if (!carousel || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    move(carousel, event.key === "ArrowLeft" ? -1 : 1);
  });

  document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-image-selection]")) return;
    const carousel = event.target.closest("[data-image-carousel]");
    if (carousel) updateSelection(carousel, event.target);
  });

  document.addEventListener("click", (event) => {
    const selectAll = event.target.closest("[data-image-selection-all]");
    if (!selectAll) return;
    const carousel = selectAll.closest("[data-image-carousel]");
    carousel.querySelectorAll("[data-image-selection]").forEach((input) => {
      input.checked = true;
    });
    updateSelection(carousel);
  });

  let touchStart = null;
  document.addEventListener("touchstart", (event) => {
    const carousel = event.target.closest?.("[data-image-carousel]");
    if (!carousel || event.touches.length !== 1) return;
    touchStart = { carousel, x: event.touches[0].clientX };
  }, { passive: true });
  document.addEventListener("touchend", (event) => {
    if (!touchStart || !event.changedTouches.length) return;
    const distance = event.changedTouches[0].clientX - touchStart.x;
    if (Math.abs(distance) >= 45) move(touchStart.carousel, distance > 0 ? -1 : 1);
    touchStart = null;
  }, { passive: true });

  window.setImageCarouselSources = setSources;
  window.initImageCarousels = initialize;
  initialize(document);
})();
