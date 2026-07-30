import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from 'vitest'

import { TooltipProvider } from '../../src/components/ui/tooltip'
import { WorkspaceProvider } from '../../src/core/workspace'
import { OverviewPage } from '../../src/features/overview/OverviewPage'
import { HealthPage } from '../../src/features/system/HealthPage'
import { seedWorkspace } from '../helpers/fixtures'

const healthRequests: string[] = []
const readyRequests: string[] = []

const server = setupServer(
  http.get('*/api/projects/demo/config/v1/admin/flags', () =>
    HttpResponse.json({ flags: [], count: 0 }),
  ),
  http.get('*/api/projects/demo/config/v1/admin/experiments', () =>
    HttpResponse.json({ experiments: [], count: 0 }),
  ),
  http.get('*/api/projects/demo/agents/v1/agents/runs', () =>
    HttpResponse.json({ runs: [], count: 0 }),
  ),
  http.post('*/api/projects/demo/query/v1/query/events/count', () =>
    HttpResponse.json({ results: [], total_events: 0, total_users: 0 }),
  ),
  http.post('*/api/projects/demo/query/v1/query/events/timeseries', () =>
    HttpResponse.json({ selector: 'page', buckets: [] }),
  ),
  http.post('*/api/projects/demo/query/v1/query/events/names', () =>
    HttpResponse.json({ events: [] }),
  ),
  http.get('*/api/projects/demo/:service/health', ({ params }) => {
    healthRequests.push(String(params.service))
    return HttpResponse.json({ status: 'ok', service: `apdl-${String(params.service)}` })
  }),
  http.get('*/api/projects/demo/:service/ready', ({ params }) => {
    readyRequests.push(String(params.service))
    return HttpResponse.json({ status: 'ready' })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

beforeEach(() => {
  localStorage.clear()
  seedWorkspace()
  healthRequests.length = 0
  readyRequests.length = 0
})

function renderPage(page: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <WorkspaceProvider initialWorkspaces={[seedWorkspace()]}>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <MemoryRouter>{page}</MemoryRouter>
        </TooltipProvider>
      </QueryClientProvider>
    </WorkspaceProvider>,
  )
}

describe('OverviewPage', () => {
  test('shows product summaries without starting service health probes', async () => {
    renderPage(<OverviewPage />)

    expect(
      screen.getByText(
        'Event activity, experiment state, flag delivery, and agent runs at a glance.',
      ),
    ).toBeVisible()
    for (const heading of [
      'Event throughput',
      'Experiments',
      'Feature flags',
      'Agents',
      'Realtime stream',
    ]) {
      expect(screen.getByText(heading)).toBeVisible()
    }

    expect(await screen.findByText('No runs yet.')).toBeVisible()
    expect(healthRequests).toEqual([])
    expect(readyRequests).toEqual([])
    for (const service of ['Ingestion', 'Config', 'Query']) {
      expect(screen.queryByText(service)).not.toBeInTheDocument()
    }
  })
})

describe('HealthPage', () => {
  test('remains the dedicated polling surface for every service', async () => {
    renderPage(<HealthPage />)

    expect(screen.getByRole('heading', { name: 'System health' })).toBeVisible()
    for (const service of ['Ingestion', 'Config', 'Query', 'Agents']) {
      expect(screen.getByText(service)).toBeVisible()
      expect(screen.getByRole('button', { name: `Refresh ${service} health` })).toBeVisible()
    }
    expect(screen.getByText('Console SSE connection')).toBeVisible()

    await waitFor(() =>
      expect([...healthRequests].sort()).toEqual(['agents', 'config', 'ingestion', 'query']),
    )
    expect([...readyRequests].sort()).toEqual(['agents', 'config', 'query'])
  })
})
