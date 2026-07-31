/**
 * The WebGL layer that sits behind every page.
 *
 * Chrome is the whole point of a Y2K scene, and chrome is nothing but a
 * reflection — a metal material with no environment to reflect renders as a
 * flat grey blob. So the scene builds a small procedural gradient env map
 * (magenta above, cyan below, white horizon) and runs it through
 * PMREMGenerator; that is what makes the objects read as polished metal
 * without shipping an HDR file.
 *
 * Cost control, since this runs under the entire app:
 *   * pixel ratio capped at 1.75 — beyond that a full-viewport canvas costs
 *     more than the effect is worth on a hidpi laptop
 *   * the RAF loop stops entirely when the tab is hidden
 *   * `prefers-reduced-motion` renders one static frame and stops
 *   * every geometry/material/texture is disposed on unmount, and the
 *     WebGL context is explicitly released
 */

import { useEffect, useRef } from 'react';
import * as THREE from 'three';

/** Builds the gradient environment that the chrome materials reflect. */
function createEnvironment(renderer: THREE.WebGLRenderer): THREE.Texture {
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;

  const gradient = ctx.createLinearGradient(0, 0, 0, size);
  gradient.addColorStop(0, '#ff6ad5');
  gradient.addColorStop(0.35, '#c774e8');
  gradient.addColorStop(0.5, '#ffffff');
  gradient.addColorStop(0.65, '#8795e8');
  gradient.addColorStop(1, '#00b6ff');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);

  // A few bright bands become specular streaks across the metal.
  ctx.globalAlpha = 0.5;
  ctx.fillStyle = '#ffffff';
  for (let i = 0; i < 5; i++) {
    ctx.fillRect(0, (i / 5) * size + 6, size, 3);
  }
  ctx.globalAlpha = 1;

  const texture = new THREE.CanvasTexture(canvas);
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.colorSpace = THREE.SRGBColorSpace;

  const pmrem = new THREE.PMREMGenerator(renderer);
  const envMap = pmrem.fromEquirectangular(texture).texture;

  pmrem.dispose();
  texture.dispose();
  return envMap;
}

interface Floater {
  mesh: THREE.Mesh;
  spin: THREE.Vector3;
  bobPhase: number;
  bobAmplitude: number;
  baseY: number;
}

export default function Stage3D() {
  const hostRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = hostRef.current;
    if (!canvas) return;

    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas,
        alpha: true,
        antialias: true,
        powerPreference: 'high-performance',
      });
    } catch {
      // No WebGL (old browser, blocklisted driver): the CSS scenery behind
      // this canvas already carries the look, so bail out quietly.
      return;
    }

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    renderer.setSize(window.innerWidth, window.innerHeight, false);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      52,
      window.innerWidth / window.innerHeight,
      0.1,
      100,
    );
    camera.position.set(0, 0, 15);

    const envMap = createEnvironment(renderer);
    scene.environment = envMap;

    // Lights still matter for the non-metal (glass/plastic) pieces.
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));

    const keyLight = new THREE.DirectionalLight(0xffb6f0, 2.1);
    keyLight.position.set(5, 6, 8);
    scene.add(keyLight);

    const rimLight = new THREE.DirectionalLight(0x00b6ff, 1.5);
    rimLight.position.set(-7, -3, 4);
    scene.add(rimLight);

    const world = new THREE.Group();
    scene.add(world);

    const disposables: Array<{ dispose: () => void }> = [envMap];
    const floaters: Floater[] = [];

    const chrome = new THREE.MeshPhysicalMaterial({
      color: 0xffffff,
      metalness: 1,
      roughness: 0.08,
      envMapIntensity: 1.9,
    });

    const holo = new THREE.MeshPhysicalMaterial({
      color: 0xff8fe0,
      metalness: 0.95,
      roughness: 0.16,
      iridescence: 1,
      iridescenceIOR: 1.9,
      iridescenceThicknessRange: [120, 780],
      envMapIntensity: 1.7,
    });

    const glass = new THREE.MeshPhysicalMaterial({
      color: 0xb6f0ff,
      metalness: 0,
      roughness: 0.05,
      transmission: 0.92,
      thickness: 1.4,
      ior: 1.45,
      transparent: true,
      opacity: 0.85,
      envMapIntensity: 1.4,
    });

    disposables.push(chrome, holo, glass);

    const addFloater = (
      geometry: THREE.BufferGeometry,
      material: THREE.Material,
      position: [number, number, number],
      scale = 1,
    ) => {
      disposables.push(geometry);
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(...position);
      mesh.scale.setScalar(scale);
      mesh.rotation.set(Math.random() * Math.PI, Math.random() * Math.PI, 0);
      world.add(mesh);
      floaters.push({
        mesh,
        spin: new THREE.Vector3(
          (Math.random() - 0.5) * 0.16,
          (Math.random() - 0.5) * 0.22,
          (Math.random() - 0.5) * 0.1,
        ),
        bobPhase: Math.random() * Math.PI * 2,
        bobAmplitude: 0.28 + Math.random() * 0.4,
        baseY: position[1],
      });
      return mesh;
    };

    // The hero object — a chrome torus knot, the single most Y2K shape there is.
    addFloater(new THREE.TorusKnotGeometry(1.5, 0.46, 190, 32), chrome, [0, 0.4, 0], 1);

    // Iridescent CDs: very flat cylinders, edge-on they catch the light.
    for (const [x, y, z, s] of [
      [-6.4, 2.4, -3, 1],
      [6.2, -2.2, -2, 0.85],
      [4.6, 3.4, -6, 0.7],
    ] as const) {
      addFloater(new THREE.CylinderGeometry(1.5, 1.5, 0.045, 64), holo, [x, y, z], s);
    }

    // Floating "books" — boxes with a book's proportions.
    for (const [x, y, z] of [
      [-5.2, -2.8, -1],
      [5.8, 2.9, -4],
      [-3.4, 3.6, -5],
    ] as const) {
      addFloater(new THREE.BoxGeometry(1.15, 1.65, 0.26), holo, [x, y, z], 1);
    }

    // Glass beads for depth.
    for (const [x, y, z, s] of [
      [-7.6, -0.4, -4, 0.8],
      [7.4, 0.9, -5, 0.62],
      [1.8, -4.1, -3, 0.5],
    ] as const) {
      addFloater(new THREE.IcosahedronGeometry(1, 1), glass, [x, y, z], s);
    }

    // Starfield.
    const starCount = 900;
    const starPositions = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount; i++) {
      starPositions[i * 3] = (Math.random() - 0.5) * 60;
      starPositions[i * 3 + 1] = (Math.random() - 0.5) * 40;
      starPositions[i * 3 + 2] = -Math.random() * 40 - 5;
    }
    const starGeometry = new THREE.BufferGeometry();
    starGeometry.setAttribute(
      'position',
      new THREE.BufferAttribute(starPositions, 3),
    );
    const starMaterial = new THREE.PointsMaterial({
      color: 0xffffff,
      size: 0.09,
      transparent: true,
      opacity: 0.75,
      sizeAttenuation: true,
    });
    const stars = new THREE.Points(starGeometry, starMaterial);
    scene.add(stars);
    disposables.push(starGeometry, starMaterial);

    // --- Interaction: gentle parallax toward the pointer ------------------
    const pointer = { x: 0, y: 0 };
    const target = { x: 0, y: 0 };

    const onPointerMove = (event: PointerEvent) => {
      target.x = (event.clientX / window.innerWidth) * 2 - 1;
      target.y = (event.clientY / window.innerHeight) * 2 - 1;
    };
    window.addEventListener('pointermove', onPointerMove, { passive: true });

    // Scroll drives a slow rotation, so the scene feels attached to the page.
    let scrollNorm = 0;
    const onScroll = () => {
      const max = Math.max(
        1,
        document.documentElement.scrollHeight - window.innerHeight,
      );
      scrollNorm = window.scrollY / max;
    };
    window.addEventListener('scroll', onScroll, { passive: true });

    const onResize = () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight, false);
    };
    window.addEventListener('resize', onResize);

    // --- Loop -------------------------------------------------------------
    const clock = new THREE.Clock();
    let frame = 0;
    let running = true;

    const renderFrame = () => {
      const elapsed = clock.getElapsedTime();
      const delta = Math.min(clock.getDelta(), 0.05);

      // Ease toward the pointer rather than tracking it exactly.
      pointer.x += (target.x - pointer.x) * 0.045;
      pointer.y += (target.y - pointer.y) * 0.045;

      world.rotation.y = pointer.x * 0.32 + scrollNorm * Math.PI * 0.5;
      world.rotation.x = pointer.y * 0.18;

      camera.position.x = pointer.x * 1.1;
      camera.position.y = -pointer.y * 0.8;
      camera.lookAt(0, 0, 0);

      for (const floater of floaters) {
        floater.mesh.rotation.x += floater.spin.x * delta;
        floater.mesh.rotation.y += floater.spin.y * delta;
        floater.mesh.rotation.z += floater.spin.z * delta;
        floater.mesh.position.y =
          floater.baseY +
          Math.sin(elapsed * 0.6 + floater.bobPhase) * floater.bobAmplitude;
      }

      stars.rotation.z = elapsed * 0.008;

      renderer.render(scene, camera);
    };

    const loop = () => {
      if (!running) return;
      frame = requestAnimationFrame(loop);
      renderFrame();
    };

    if (reduceMotion) {
      renderFrame();
    } else {
      loop();
    }

    // Stop burning GPU on a background tab.
    const onVisibility = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(frame);
      } else if (!reduceMotion && !running) {
        running = true;
        clock.getDelta(); // discard the gap so nothing jumps
        loop();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      running = false;
      cancelAnimationFrame(frame);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('visibilitychange', onVisibility);

      for (const item of disposables) item.dispose();
      renderer.dispose();
      // Without this the context lingers and browsers cap you at ~16 of them.
      renderer.forceContextLoss();
    };
  }, []);

  return (
    <div className="stage" aria-hidden="true">
      <div className="stage__blob stage__blob--a" />
      <div className="stage__blob stage__blob--b" />
      <div className="stage__blob stage__blob--c" />
      <canvas ref={hostRef} className="stage__canvas" />
      <div className="stage__grid" />
      <div className="stage__scan" />
    </div>
  );
}
