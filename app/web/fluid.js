(() => {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const states = new WeakMap();

  function stateFor(element) {
    if (!states.has(element)) {
      states.set(element, {
        x: 0, y: 0, scale: 1, opacity: 1,
        vx: 0, vy: 0, vScale: 0, vOpacity: 0,
        target: {}, frame: 0, last: 0,
      });
    }
    return states.get(element);
  }

  function render(element, state) {
    element.style.transform = `translate3d(${state.x}px, ${state.y}px, 0) scale(${state.scale})`;
    element.style.opacity = String(state.opacity);
  }

  function stepSpring(value, velocity, target, dt, stiffness, damping) {
    const acceleration = stiffness * (target - value) - damping * velocity;
    const nextVelocity = velocity + acceleration * dt;
    return [value + nextVelocity * dt, nextVelocity];
  }

  function springTo(element, target, options = {}) {
    const state = stateFor(element);
    window.cancelAnimationFrame(state.frame);
    state.resolve?.({ cancelled: true });
    state.resolve = null;
    state.target = { ...state.target, ...target };
    if (options.velocity) {
      state.vx = options.velocity.x ?? state.vx;
      state.vy = options.velocity.y ?? state.vy;
    }
    const stiffness = options.stiffness ?? 700;
    const damping = options.damping ?? 53;

    if (reducedMotion.matches) {
      state.reducedAnimation?.cancel();
      const currentOpacity = Number.parseFloat(getComputedStyle(element).opacity) || state.opacity;
      Object.assign(state, target);
      state.x = 0;
      state.y = 0;
      state.scale = 1;
      render(element, state);
      if (target.opacity === undefined || Math.abs(currentOpacity - target.opacity) < 0.01) {
        return Promise.resolve();
      }
      state.reducedAnimation = element.animate(
        [{ opacity: currentOpacity }, { opacity: target.opacity }],
        { duration: 120, easing: "linear", fill: "forwards" }
      );
      return state.reducedAnimation.finished.catch(() => undefined);
    }

    return new Promise((resolve) => {
      state.resolve = resolve;
      function tick(now) {
        const dt = Math.min((now - (state.last || now)) / 1000, 0.034);
        state.last = now;
        let settled = true;
        for (const [property, velocityKey] of [["x", "vx"], ["y", "vy"], ["scale", "vScale"], ["opacity", "vOpacity"]]) {
          if (state.target[property] === undefined) continue;
          [state[property], state[velocityKey]] = stepSpring(
            state[property], state[velocityKey], state.target[property], dt, stiffness, damping
          );
          if (Math.abs(state[property] - state.target[property]) > 0.001 || Math.abs(state[velocityKey]) > 0.01) settled = false;
        }
        render(element, state);
        if (settled) {
          Object.assign(state, state.target);
          state.vx = state.vy = state.vScale = state.vOpacity = 0;
          state.last = 0;
          state.resolve = null;
          render(element, state);
          resolve();
          return;
        }
        state.frame = window.requestAnimationFrame(tick);
      }
      state.frame = window.requestAnimationFrame(tick);
    });
  }

  function setNow(element, values) {
    const state = stateFor(element);
    window.cancelAnimationFrame(state.frame);
    Object.assign(state, values);
    render(element, state);
  }

  function bindPress(root = document) {
    root.querySelectorAll(".fluid-press, button, [role='button']").forEach((element) => {
      if (element.dataset.fluidBound) return;
      element.dataset.fluidBound = "true";
      const release = () => springTo(element, { scale: 1 }, { stiffness: 700, damping: 53 });
      element.addEventListener("pointerdown", (event) => {
        if (element.disabled || event.button !== 0) return;
        element.setPointerCapture?.(event.pointerId);
        setNow(element, { scale: 0.97 });
      });
      element.addEventListener("pointerup", release);
      element.addEventListener("pointercancel", release);
      element.addEventListener("lostpointercapture", release);
    });
  }

  function reveal(element, visible) {
    element.style.pointerEvents = visible ? "auto" : "none";
    return springTo(element, visible ? { opacity: 1, y: 0 } : { opacity: 0, y: 12 }, {
      stiffness: 700,
      damping: 53,
    });
  }

  window.LinkParseFluid = { bindPress, reducedMotion, reveal, setNow, springTo, stateFor };
})();
