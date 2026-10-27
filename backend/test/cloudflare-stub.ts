// Node stand-ins for the Workers runtime modules that `agents` imports; the code under test
// uses none of them (the stateless MCP handler needs no Durable Object).
export class DurableObject {}
export class WorkerEntrypoint {}
export class WorkflowEntrypoint {}
export class WorkflowEvent {}
export class RpcTarget {}
export class EmailMessage {}
export const env = {};
export const exports = {};
